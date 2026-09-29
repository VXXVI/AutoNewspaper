"""看板扫描执行器：参数拼装、默认 runner、任务历史与试抓探测。

默认 runner 在进程内调用 ``cli.main``，避免 ``redirect_stdout`` 污染多线程访问日志；
取消通过 ``ContextVar`` 传给同一线程内的 ``cli.main``，注入的假 runner 无需感知。
"""

from __future__ import annotations

import contextlib
import inspect
import json
import re
import threading
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable

from . import cli, clock, config, paths, report, scanner
from .web_static import MAX_TASK_HISTORY


class ScanRequestError(ValueError):
    """扫描请求在进入后台线程前即已确定无效。"""


# 协作式取消：scan_runner 在本线程内调用 cli.main，通过 ContextVar 传出取消回调，
# 避免改动可注入的 runner 签名（测试里的假 runner 无需感知）。
_SCAN_CANCEL: ContextVar[Callable[[], bool] | None] = ContextVar("npa_scan_cancel", default=None)
# 任务历史是 read-modify-write，多个扫描线程间需串行（同一进程内用锁即可）
_HISTORY_LOCK = threading.Lock()


# ---------------------------------------------------------------- 扫描执行器
_SUMMARY_RE = re.compile(
    r"扫描文章\s*(\d+)\s*篇\s*\|\s*命中\s*(\d+)\s*篇\s*\|\s*新增\s*(\d+)\s*篇\s*\|\s*耗时\s*([\d.]+)\s*s"
)


def _extract_summary(output: str) -> dict[str, Any] | None:
    """从 cli 日志的收尾统计行提炼摘要；提炼不出返回 None（不编造数字）。"""
    match = _SUMMARY_RE.search(output)
    if match is None:
        return None
    return {
        "articles": int(match.group(1)),
        "hits": int(match.group(2)),
        "new_hits": int(match.group(3)),
        "elapsed": float(match.group(4)),
    }


def build_scan_argv(data_dir: Path | str, params: dict[str, Any]) -> list[str]:
    """把看板参数拼成 ``cli.main`` 的 argv **列表**（绝不拼 shell 字符串）。"""
    argv: list[str] = ["--data-dir", str(data_dir)]
    for keyword in params.get("keywords") or []:
        argv += ["--kw", str(keyword)]
    if params.get("since") or params.get("until"):
        if params.get("since"):
            argv += ["--since", str(params["since"])]
        if params.get("until"):
            argv += ["--until", str(params["until"])]
    else:
        argv += ["--days", str(params.get("days") or config.DEFAULT_DAYS)]
    argv += ["--workers", str(params.get("workers") or config.DEFAULT_WORKERS)]
    if params.get("only"):
        argv += ["--only", str(params["only"])]
    if params.get("full"):
        argv.append("--full")
    return argv


def load_task_history(data_dir: Path | str) -> list[dict[str, Any]]:
    """读取 ``state/tasks.json`` 里的任务摘要；缺失/损坏返回空列表。"""
    try:
        with open(paths.task_history_file(Path(data_dir)), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def record_task_history(data_dir: Path | str, task: Any) -> None:
    """把一次任务摘要追加进 ``state/tasks.json``（只保留最近 ``MAX_TASK_HISTORY`` 条）。"""
    snapshot = task.snapshot()
    params = snapshot.get("params") or {}
    entry = {
        "task_id": snapshot.get("task_id"),
        "status": snapshot.get("status"),
        "started_at": snapshot.get("started_at"),
        "finished_at": snapshot.get("finished_at"),
        "duration_seconds": snapshot.get("duration_seconds"),
        "exit_code": snapshot.get("exit_code"),
        "keywords": list(params.get("keywords") or []),
        "only": str(params.get("only") or ""),
        "summary": snapshot.get("summary"),
        "error": snapshot.get("error"),
    }
    with _HISTORY_LOCK:
        history = load_task_history(data_dir)
        history.append(entry)
        with contextlib.suppress(OSError):  # 历史写入失败不应影响任务状态
            report.write_text(
                paths.task_history_file(Path(data_dir)),
                json.dumps(history[-MAX_TASK_HISTORY:], ensure_ascii=False, indent=1),
            )


def _main_accepts_cancel(main: Callable[..., Any]) -> bool:
    """判断（可能是被测试替换的）``cli.main`` 是否接受 ``cancel`` 关键字。"""
    try:
        parameters = inspect.signature(main).parameters
    except (TypeError, ValueError):
        return False
    return "cancel" in parameters or any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in parameters.values()
    )


def default_scan_runner(
    data_dir: Path | str, params: dict[str, Any], log: Callable[[str], None]
) -> dict[str, Any]:
    """默认扫描执行器：进程内调用 ``cli.main``，并通过回调实时接收日志。

    不使用 ``redirect_stdout``：它会修改整个进程的全局流，在多线程 HTTP 服务里可能把
    访问日志卷入扫描日志。argparse 的 ``SystemExit(2)`` 仍兜住转成 ``exit_code``。
    """
    argv = build_scan_argv(data_dir, params)
    lines: list[str] = []
    exit_code = 0
    cancel = _SCAN_CANCEL.get()

    def capture(message: str = "") -> None:
        line = str(message)
        lines.append(line)
        log(line)

    try:
        if cancel is not None and _main_accepts_cancel(cli.main):
            exit_code = cli.main(argv, logger=capture, cancel=cancel)
        else:
            exit_code = cli.main(argv, logger=capture)
    except SystemExit as exc:
        exit_code = _system_exit_code(exc.code)
    output = "\n".join(lines)
    return {"exit_code": exit_code, "summary": _extract_summary(output)}


def _system_exit_code(code: Any) -> int:
    """按 Python 的 ``SystemExit`` 语义归一化退出码。"""
    if code is None:
        return 0
    if isinstance(code, int):
        return int(code)
    return 1


def default_paper_probe(paper: dict[str, Any], log: Callable[[str], None]) -> dict[str, Any]:
    """默认试抓：验证入口、版面规则与文章规则，不抓正文也不写文件。"""
    day = clock.now().replace(hour=0, minute=0, second=0, microsecond=0)
    log(f"试抓「{paper['name']}」（只验证结构，不抓正文）…")
    result = scanner.probe_paper(paper, day)
    entry_code = int(result.get("entry_code", 0))
    sections = int(result.get("sections", 0))
    sample_articles = int(result.get("sample_articles", 0))
    note = str(result.get("note") or "")
    log(
        f"试抓结果：入口 {entry_code} · 版面 {sections} · 样例文章 {sample_articles}"
        + (f" · {note}" if note else "")
    )
    return {
        "entry": str(result.get("entry") or ""),
        "entry_code": entry_code,
        "sections": sections,
        "sample_articles": sample_articles,
        "note": note,
    }
