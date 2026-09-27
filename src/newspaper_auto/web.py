"""本地看板 —— 用标准库起的单页 Web 界面（零第三方依赖）。

用法::

    python3 -m newspaper_auto.web                    # http://127.0.0.1:8788
    python3 -m newspaper_auto.web --port 9000 --open
    python3 -m newspaper_auto.web --open --scan       # 打开并按上次配置立即扫描
    python3 -m newspaper_auto.web --data-dir /path/to/data

端口优先取 ``--port``（默认 8788）；8788 被占用时命令行会给出提示，可用 ``--port 0``
让系统自动分配空闲端口。看板也可在页面「高级扫描设置」里只扫选中的报社（``only``）。

特点：
- **读取 + 写入**：读取 ``hits/archive.json`` 与 ``state/seen.json`` 展示看板；
  页面顶部可通过 ``POST /api/scan`` 在看板进程内**直接触发一次真实抓取**，
  抓取结果（``hits/``、``state/``）会写回看板正在读的那个数据目录；
  ``POST /api/config`` 把关键词/天数或日期范围/并发持久化到 ``state/web_config.json``；
  ``POST /api/export`` 把当前受控筛选结果原子写入指定目录。
- **自定义报社**：``GET /api/papers`` 列出「内置 / 自定义 / 覆盖」；``POST /api/papers``
  新增或同名覆盖；``POST /api/papers/delete`` 删除自定义条目；``POST /api/papers/test``
  试抓探测；``POST /api/papers/status`` 可停用或恢复任意已接入报社。写回的是**项目根**的
  ``papers.custom.json``（配置层，与 ``--data-dir`` 无关）。
- **只允许监听环回地址**，不对外网暴露；并对所有请求做 Host / Origin 校验，
  只接受环回来源，避免浏览器里的任意网页跨站触发抓取。
- 前端为原生 HTML/CSS/JS，无构建、无 npm、无 CDN，且**零 ``innerHTML``**（数据一律
  ``textContent`` + ``createElement`` 渲染，报社名/链接均为用户输入，绝不拼 HTML）。

本模块为薄门面：实现已拆分到 ``web_static`` / ``web_dashboard`` / ``web_validate`` /
``web_papers`` / ``web_scan`` / ``web_server`` / ``web_handlers``，此处集中显式再导出，
保证 ``newspaper_auto.web`` 的公开名字与历史用法不变。
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from collections.abc import Sequence
from pathlib import Path

from . import __version__, cli, exporter, paths, web_daemon, web_static
from .web_dashboard import (
    _DASHBOARD_CACHE,
    _DASHBOARD_CACHE_LOCK,
    _GEOMETRY_NUMERIC_KEYS,
    _file_stamp,
    _normalize,
    _normalize_geometry,
    _normalize_layout,
    _read_archive,
    build_dashboard,
    load_web_config,
    save_web_config,
)
from .web_papers import _resolve_paper_by_name, build_papers_view
from .web_scan import (
    _HISTORY_LOCK,
    _SCAN_CANCEL,
    _SUMMARY_RE,
    ScanRequestError,
    _extract_summary,
    _main_accepts_cancel,
    _system_exit_code,
    build_scan_argv,
    default_paper_probe,
    default_scan_runner,
    load_task_history,
    record_task_history,
)
from .web_server import DashboardHandler, DashboardServer, _hostname_of, create_server
from .web_static import (
    DATE_RE,
    DEFAULT_HOST,
    DEFAULT_PORT,
    EXPORT_SORTS,
    LOOPBACK_HOSTS,
    MAX_BODY_BYTES,
    MAX_EXPORT_FILTER_ITEMS,
    MAX_EXPORT_QUERY_LEN,
    MAX_KEYWORD_LEN,
    MAX_KEYWORDS,
    MAX_LOG_LINES,
    MAX_TASK_HISTORY,
    MAX_WORKERS,
    SCAN_EVENT_INTERVAL,
    SCAN_MODES,
    STATIC_DIR,
    STATIC_MIN_GZIP_BYTES,
    STATIC_TYPES,
    _accepts_gzip,
    _etag_matches,
)
from .web_tasks import ScanTask
from .web_validate import (
    _clean_export_filter_list,
    _clean_keywords,
    _clean_only,
    _parse_iso_date,
    _valid_days,
    _valid_workers,
    filter_export_records,
    validate_export_request,
    validate_scan_params,
    validate_web_config,
)

__all__ = [
    # web_static
    "DATE_RE",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "EXPORT_SORTS",
    "LOOPBACK_HOSTS",
    "MAX_BODY_BYTES",
    "MAX_EXPORT_FILTER_ITEMS",
    "MAX_EXPORT_QUERY_LEN",
    "MAX_KEYWORDS",
    "MAX_KEYWORD_LEN",
    "MAX_LOG_LINES",
    "MAX_TASK_HISTORY",
    "MAX_WORKERS",
    "SCAN_EVENT_INTERVAL",
    "SCAN_MODES",
    "STATIC_DIR",
    "STATIC_MIN_GZIP_BYTES",
    "STATIC_TYPES",
    # web_dashboard
    "_DASHBOARD_CACHE",
    "_DASHBOARD_CACHE_LOCK",
    "_GEOMETRY_NUMERIC_KEYS",
    # web_scan
    "_HISTORY_LOCK",
    "_SCAN_CANCEL",
    "_SUMMARY_RE",
    # web_server
    "DashboardHandler",
    "DashboardServer",
    "ScanRequestError",
    # web_tasks
    "ScanTask",
    "_accepts_gzip",
    # web_validate
    "_clean_export_filter_list",
    "_clean_keywords",
    "_clean_only",
    "_etag_matches",
    "_extract_summary",
    "_file_stamp",
    "_hostname_of",
    "_main_accepts_cancel",
    "_normalize",
    "_normalize_geometry",
    "_normalize_layout",
    "_parse_iso_date",
    "_read_archive",
    # web_papers
    "_resolve_paper_by_name",
    "_system_exit_code",
    "_valid_days",
    "_valid_workers",
    "build_dashboard",
    "build_papers_view",
    "build_scan_argv",
    # 模块（测试会 monkeypatch 这些模块属性）
    "cli",
    "create_server",
    "default_paper_probe",
    "default_scan_runner",
    "exporter",
    "filter_export_records",
    "load_task_history",
    "load_web_config",
    "paths",
    "record_task_history",
    "save_web_config",
    "sys",
    "threading",
    "validate_export_request",
    "validate_scan_params",
    "validate_web_config",
    "web_daemon",
    "web_static",
]


def _static_dir() -> Path:
    """静态资源目录（兼容旧调用）：以本模块 ``__file__`` 为准，支持冻结回退。"""
    return web_static._static_dir(__file__)


# ---------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m newspaper_auto.web",
        description="数字报监测 · 本地看板（可配置关键词/日期范围、管理自定义报社、一键触发扫描）",
    )
    parser.add_argument("--data-dir", default=None, help="数据目录（含 hits/ 与 state/），默认自动探测项目根")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"监听地址（默认 {DEFAULT_HOST}，仅本机可访问）")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"监听端口（默认 {DEFAULT_PORT}，0 表示自动分配）"
    )
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    parser.add_argument(
        "--scan",
        action="store_true",
        help="启动后按 state/web_config.json（不存在则用默认设置）立即扫描",
    )
    parser.add_argument(
        "--detach",
        action="store_true",
        help="在后台运行（脱离当前控制台，关闭窗口不影响看板）；已在运行则直接复用",
    )
    parser.add_argument("--stop", action="store_true", help="停止后台运行的看板")
    parser.add_argument("--status", action="store_true", help="查看看板是否在运行")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _stop_hint() -> str:
    """教用户怎么停止后台看板（源码运行与冻结运行的命令不同）。"""
    if getattr(sys, "frozen", False):
        return "NewspaperAuto.exe dashboard --stop"
    return "python -m newspaper_auto.web --stop"


def _pause_if_console() -> None:
    """只在「人类双击」场景下停一下，避免窗口一闪而过看不到提示。

    判据是 ``--open``（自动开浏览器 = 面向人的调用）**且** 标准输入是终端；
    脚本里 ``dashboard --detach`` 不会被打断。
    """
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            input("按回车键关闭本窗口…")
    except (EOFError, OSError, KeyboardInterrupt):
        pass


def _detach(data_dir: Path, args: argparse.Namespace) -> int:
    """后台启动看板并按需打开浏览器。"""
    state = web_daemon.launch(data_dir, args.host, args.port, scan=args.scan)
    if not state.get("running"):
        print(f"启动失败：{state.get('reason', '未知原因')}", file=sys.stderr)
        print("可换端口重试（--port 8789），或查看 logs/dashboard.log。", file=sys.stderr)
        return 1
    if args.open:
        webbrowser.open(state["url"])
    print(f"看板{'已在后台运行' if state.get('reused') else '已启动'}：{state['url']}（pid {state['pid']}）")
    print(f"关闭本窗口不会停止看板；要停止请运行：{_stop_hint()}")
    if args.open:
        _pause_if_console()
    return 0


def main(argv: Sequence[str] | None = None, *, default_data_dir: Path | None = None) -> int:
    # 后台运行时 stdout/stderr 是文件（块缓冲），日志会「什么都看不到」；
    # 改成行缓冲，出问题时有据可查。
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(line_buffering=True)
        except (OSError, ValueError):
            pass

    args = build_parser().parse_args(argv)
    data_dir = paths.resolve_data_dir(args.data_dir, default_data_dir)

    if args.stop:
        result = web_daemon.stop(data_dir)
        if result.get("stopped"):
            print(f"已停止后台看板（pid {result.get('pid')}）。")
            return 0
        print(f"未停止：{result.get('reason', '未知原因')}。")
        return 1
    if args.status:
        state = web_daemon.status(data_dir)
        if state["running"]:
            print(f"看板正在运行：{state['url']}（pid {state['pid']}，v{state['version']}）")
            return 0
        print("看板未在运行。")
        return 1
    if args.detach:
        return _detach(data_dir, args)

    try:
        server = create_server(data_dir, args.host, args.port)
    except ValueError as exc:
        print(f"启动失败：{exc}。", file=sys.stderr)
        print("如需从 VPS 外访问，请使用 SSH 端口转发，不要直接暴露看板端口。", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"启动失败：{args.host}:{args.port} 不可用（{exc}）。", file=sys.stderr)
        print("可换端口重试，例如 --port 8789，或用 --port 0 让系统自动分配。", file=sys.stderr)
        return 1

    host, port = server.server_address[0], server.server_address[1]
    url = f"http://{host}:{port}/"
    # 落盘运行状态：--status / --stop 与「已在运行则复用」都依赖它
    web_daemon.write_state(data_dir, host, port)

    print(f"看板已启动：{url}")
    print(f"数据目录：{data_dir}")
    print(f"数据源：{paths.archive_file(data_dir)}")
    if args.scan:
        params = load_web_config(data_dir)
        selection_error = server.paper_selection_error(params["only"])
        if selection_error:
            print(f"自动扫描未启动：{selection_error}。", file=sys.stderr)
        else:
            try:
                task = server.start_scan(params)
            except ScanRequestError as exc:
                print(f"自动扫描未启动：{exc}。", file=sys.stderr)
            except Exception as exc:
                print(f"自动扫描启动失败：{exc}。", file=sys.stderr)
            else:
                if task is None:  # 理论上首次启动不会发生，保留明确失败信息
                    print("自动扫描未启动：已有扫描任务。", file=sys.stderr)
                else:
                    print("自动扫描已启动：页面会显示实时进度。")
    else:
        print("可在页面顶部填写关键词并开始扫描；按 Ctrl+C 停止")

    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
        web_daemon.clear_state(data_dir, os.getpid())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
