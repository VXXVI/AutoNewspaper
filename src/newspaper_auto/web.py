"""本地看板 —— 用标准库起的单页 Web 界面（零第三方依赖）。

用法::

    python3 -m newspaper_auto.web                    # http://127.0.0.1:8787
    python3 -m newspaper_auto.web --port 9000 --open
    python3 -m newspaper_auto.web --data-dir /path/to/data

特点：
- **读取 + 写入**：读取 ``hits/archive.json`` 与 ``state/seen.json`` 展示看板；
  并新增「扫描设置」卡片，可通过 ``POST /api/scan`` 在看板进程内**直接触发一次真实抓取**，
  抓取结果（``hits/``、``state/``）会写回看板正在读的那个数据目录；
  ``POST /api/config`` 把关键词/天数/并发持久化到 ``state/web_config.json``。
- **默认只监听 127.0.0.1**，不对外网暴露；并对所有请求做 Host / Origin 校验，
  只接受环回来源，避免浏览器里的任意网页跨站触发抓取。
- 前端为原生 HTML/CSS/JS，无构建、无 npm、无 CDN。
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
import threading
import webbrowser
from collections.abc import Callable, Sequence
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import __version__, cli, config, paths, report, state

STATIC_DIR = Path(__file__).parent / "static"
STATIC_TYPES = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787

# 允许的 Host / Origin 主机名（环回）。看板只在本机使用，绝不接受外部来源。
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
# POST 请求体上限：扫描配置本身极小，给足冗余但绝不无限读。
MAX_BODY_BYTES = 16384
# 扫描日志最多保留的行数（超出丢最旧的）
MAX_LOG_LINES = 300
MAX_KEYWORDS = 10
MAX_KEYWORD_LEN = 50
MAX_WORKERS = 16
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------- 数据装配
def _read_archive(path: Path) -> list[Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


_GEOMETRY_NUMERIC_KEYS = ("x1", "y1", "x2", "y2", "top", "bottom", "index", "total")


def _normalize_geometry(raw: Any) -> dict[str, int | float | str] | None:
    """收敛 ``geometry``；旧归档没有该字段（或残缺）时返回 None，前端据此不显示位置。

    必需字段只要 ``index`` / ``total``：纯链接式版面（如南方日报）按链接顺序兜底时根本
    没有 ``top`` 等纵向坐标，只靠 ``top`` 判空会把整块位置信息丢掉。``x1..y2`` / ``top``
    / ``bottom`` 有就带上、没有就不带；``basis``（``area`` / ``order``）作为可选项一并透出。
    旧归档的无 geometry 或残缺 geometry（缺 ``index``/``total``）仍返回 None。
    """
    if not isinstance(raw, dict):
        return None
    geometry: dict[str, int | float | str] = {
        key: raw[key]
        for key in _GEOMETRY_NUMERIC_KEYS
        if isinstance(raw.get(key), (int, float)) and not isinstance(raw.get(key), bool)
    }
    if not {"index", "total"} <= geometry.keys():
        return None
    basis = raw.get("basis")
    if isinstance(basis, str) and basis:
        geometry["basis"] = basis
    return geometry


def _normalize(item: Any) -> dict[str, Any] | None:
    """把归档条目收敛成前端需要的形状；丢弃无 URL 的脏数据。"""
    if not isinstance(item, dict) or not item.get("url"):
        return None
    try:
        text_len = int(item.get("text_len") or 0)
    except (TypeError, ValueError):
        text_len = 0
    return {
        "paper": str(item.get("paper", "")),
        "date": str(item.get("date", "")),
        "section": str(item.get("section") or ""),
        "section_no": str(item.get("section_no") or ""),
        "title": str(item.get("title", "")),
        "url": str(item.get("url", "")),
        "text_len": text_len,
        "snippets": [str(s) for s in (item.get("snippets") or []) if s],
        "matched": [str(k) for k in (item.get("matched") or []) if k],
        "geometry": _normalize_geometry(item.get("geometry")),
    }


def build_dashboard(data_dir: Path | str, now: datetime | None = None) -> dict[str, Any]:
    """组装看板所需的全部数据（纯读取，无副作用）。"""
    now = now or datetime.now()
    data_dir = Path(data_dir)
    archive_path = paths.archive_file(data_dir)

    records = [rec for rec in (_normalize(item) for item in _read_archive(archive_path)) if rec]
    records.sort(key=lambda r: (r["date"], r["paper"], r["url"]), reverse=True)

    seen = state.load_seen(paths.state_file(data_dir))
    seen_counts = {kw: len(bucket) for kw, bucket in seen.items() if isinstance(bucket, dict)}

    date_counts: dict[str, int] = {}
    paper_counts: dict[str, int] = {}
    for rec in records:
        date_counts[rec["date"]] = date_counts.get(rec["date"], 0) + 1
        paper_counts[rec["paper"]] = paper_counts.get(rec["paper"], 0) + 1

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "version": __version__,
        "data_dir": str(data_dir),
        "archive_file": str(archive_path),
        "count": len(records),
        "papers": sorted(paper_counts, key=lambda name: (-paper_counts[name], name)),
        "paper_counts": paper_counts,
        "keywords": sorted({k for rec in records for k in rec["matched"]}),
        "seen_counts": seen_counts,
        "date_counts": date_counts,
        "records": records,
    }


# ---------------------------------------------------------------- 参数校验
def _is_int(value: Any) -> bool:
    """严格的 int 判定（``bool`` 是 int 的子类，必须排除）。"""
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_iso_date(text: Any) -> datetime | None:
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not DATE_RE.match(stripped):
        return None
    try:
        return datetime.strptime(stripped, "%Y-%m-%d")
    except ValueError:
        return None


def _clean_keywords(raw: Any) -> tuple[list[str] | None, str | None]:
    if not isinstance(raw, list) or not raw:
        return None, "keywords 必须是非空数组"
    cleaned: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            return None, "keywords 的每个元素都必须是字符串"
        value = item.strip()
        if not value:
            return None, "keywords 不能包含空字符串"
        if len(value) > MAX_KEYWORD_LEN:
            return None, f"单个关键词不能超过 {MAX_KEYWORD_LEN} 个字符"
        cleaned.append(value)
    if len(cleaned) > MAX_KEYWORDS:
        return None, f"keywords 最多 {MAX_KEYWORDS} 个"
    return cleaned, None


def validate_scan_params(payload: Any) -> tuple[dict[str, Any] | None, str | None]:
    """校验 ``POST /api/scan`` 的请求体；返回 ``(归一化参数, 错误说明)``。

    满足条件时错误为 None；否则参数为 None 且错误是给用户看的中文说明。
    """
    if not isinstance(payload, dict):
        return None, "请求体必须是 JSON 对象"
    keywords, error = _clean_keywords(payload.get("keywords"))
    if error:
        return None, error

    days = payload.get("days")
    since = payload.get("since")
    until = payload.get("until")
    use_range = since is not None or until is not None
    if days is not None and use_range:
        return None, "days 不能与 since/until 同时使用"
    if days is not None and (not _is_int(days) or not 1 <= days <= config.MAX_RANGE_DAYS):
        return None, f"days 必须是 1~{config.MAX_RANGE_DAYS} 之间的整数"

    since_text: str | None = None
    until_text: str | None = None
    if use_range:
        since_dt = None
        until_dt = None
        if since is not None:
            since_dt = _parse_iso_date(since)
            if since_dt is None:
                return None, "since 必须是合法的 YYYY-MM-DD 日期"
            since_text = since_dt.strftime("%Y-%m-%d")
        if until is not None:
            until_dt = _parse_iso_date(until)
            if until_dt is None:
                return None, "until 必须是合法的 YYYY-MM-DD 日期"
            until_text = until_dt.strftime("%Y-%m-%d")
        if since_dt is not None and until_dt is not None:
            if since_dt > until_dt:
                return None, "since 不能晚于 until"
            if (until_dt - since_dt).days + 1 > config.MAX_RANGE_DAYS:
                return None, f"日期跨度不能超过 {config.MAX_RANGE_DAYS} 天"

    workers = payload.get("workers")
    if workers is None:
        workers = config.DEFAULT_WORKERS
    elif not _is_int(workers) or not 1 <= workers <= MAX_WORKERS:
        return None, f"workers 必须是 1~{MAX_WORKERS} 之间的整数"

    if not use_range and days is None:
        days = config.DEFAULT_DAYS

    only = payload.get("only") or ""
    if not isinstance(only, str):
        return None, "only 必须是字符串"

    full = payload.get("full", False)
    if not isinstance(full, bool):
        return None, "full 必须是布尔值"

    return {
        "keywords": keywords,
        "days": None if use_range else days,
        "since": since_text,
        "until": until_text,
        "workers": workers,
        "only": only.strip(),
        "full": full,
    }, None


def validate_web_config(payload: Any) -> tuple[dict[str, Any] | None, str | None]:
    """校验 ``POST /api/config`` 的请求体（关键词 / 天数 / 并发）。"""
    if not isinstance(payload, dict):
        return None, "请求体必须是 JSON 对象"
    keywords, error = _clean_keywords(payload.get("keywords"))
    if error:
        return None, error
    days = payload.get("days", config.DEFAULT_DAYS)
    if not _is_int(days) or not 1 <= days <= config.MAX_RANGE_DAYS:
        return None, f"days 必须是 1~{config.MAX_RANGE_DAYS} 之间的整数"
    workers = payload.get("workers", config.DEFAULT_WORKERS)
    if not _is_int(workers) or not 1 <= workers <= MAX_WORKERS:
        return None, f"workers 必须是 1~{MAX_WORKERS} 之间的整数"
    return {"keywords": keywords, "days": days, "workers": workers}, None


def load_web_config(data_dir: Path | str) -> dict[str, Any]:
    """读取看板配置；文件缺失或内容非法时回落到默认值。"""
    defaults = {
        "keywords": list(config.DEFAULT_KEYWORDS),
        "days": config.DEFAULT_DAYS,
        "workers": config.DEFAULT_WORKERS,
    }
    try:
        with open(paths.web_config_file(Path(data_dir)), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return defaults
    if not isinstance(data, dict):
        return defaults

    keywords = data.get("keywords")
    if isinstance(keywords, list) and keywords and all(isinstance(k, str) and k.strip() for k in keywords):
        keywords = [k.strip() for k in keywords][:MAX_KEYWORDS]
    else:
        keywords = defaults["keywords"]

    days = data.get("days")
    if not _is_int(days) or not 1 <= days <= config.MAX_RANGE_DAYS:
        days = defaults["days"]

    workers = data.get("workers")
    if not _is_int(workers) or not 1 <= workers <= MAX_WORKERS:
        workers = defaults["workers"]

    return {"keywords": keywords, "days": days, "workers": workers}


def save_web_config(data_dir: Path | str, cfg: dict[str, Any]) -> None:
    """把配置写回 ``state/web_config.json``（自动建目录）。"""
    report.write_text(
        paths.web_config_file(Path(data_dir)),
        json.dumps(cfg, ensure_ascii=False, indent=1),
    )


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


def default_scan_runner(
    data_dir: Path | str, params: dict[str, Any], log: Callable[[str], None]
) -> dict[str, Any]:
    """默认扫描执行器：在看板进程内调用 ``cli.main``（不做 subprocess）。

    ``cli.main`` 的日志走 stdout/stderr，这里统一重定向并逐行喂给 ``log``；
    argparse 的 ``SystemExit(2)`` 被兜住转成 ``exit_code``，绝不让工作线程炸掉。
    """
    argv = build_scan_argv(data_dir, params)
    buffer = io.StringIO()
    output = ""
    exit_code = 0
    try:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            exit_code = cli.main(argv)
    except SystemExit as exc:
        code = exc.code
        exit_code = code if isinstance(code, int) else (0 if code is None else 2)
    finally:
        output = buffer.getvalue()
        for line in output.splitlines():
            log(line)
    return {"exit_code": exit_code, "summary": _extract_summary(output)}


# ---------------------------------------------------------------- 扫描任务
class ScanTask:
    """一次扫描任务的运行状态（同一时刻最多一个，由 server.scan_lock 守护）。"""

    def __init__(self) -> None:
        self.status = "idle"
        self.params: dict[str, Any] = {}
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.log: list[str] = []
        self.summary: dict[str, Any] | None = None
        self.exit_code: int | None = None
        self.error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "params": dict(self.params),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "log": list(self.log),
            "summary": self.summary,
            "exit_code": self.exit_code,
            "error": self.error,
        }


def _now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- HTTP
class DashboardServer(ThreadingHTTPServer):
    """把数据目录、扫描执行器与任务状态挂在 server 上。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        data_dir: Path | str,
        scan_runner: Callable[[Path, dict[str, Any], Callable[[str], None]], dict[str, Any]] | None = None,
    ):
        super().__init__(address, DashboardHandler)
        self.data_dir = Path(data_dir)
        # 执行器可注入：测试用假 runner，绝不碰网络
        self.scan_runner = scan_runner or default_scan_runner
        self.scan_lock = threading.Lock()
        self.scan_task = ScanTask()

    def start_scan(self, params: dict[str, Any]) -> ScanTask | None:
        """尝试占用任务锁；成功则起后台线程，失败（已有任务）返回 None，绝不排队等待。"""
        if not self.scan_lock.acquire(blocking=False):
            return None
        task = ScanTask()
        task.status = "running"
        task.params = dict(params)
        task.started_at = _now_stamp()
        self.scan_task = task
        threading.Thread(target=self._run_scan, args=(task, params), daemon=True).start()
        return task

    def _run_scan(self, task: ScanTask, params: dict[str, Any]) -> None:
        def log(line: str) -> None:
            task.log.append(line)
            if len(task.log) > MAX_LOG_LINES:
                del task.log[: len(task.log) - MAX_LOG_LINES]
            sys.stderr.write(f"[扫描] {line}\n")

        try:
            result = self.scan_runner(self.data_dir, dict(params), log)
            task.exit_code = int(result.get("exit_code", 0))
            task.summary = result.get("summary")
            task.status = "done"
        except SystemExit as exc:  # 注入的 runner 也可能抛 SystemExit
            code = exc.code
            task.exit_code = code if isinstance(code, int) else 1
            task.status = "done"
        except Exception as exc:  # 兜底：runner 抛错也必须释放任务锁
            task.error = str(exc) or exc.__class__.__name__
            task.status = "error"
        finally:
            task.finished_at = _now_stamp()
            self.scan_lock.release()


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = f"NewsPaperAuto/{__version__}"
    protocol_version = "HTTP/1.1"

    # -- 路由 --------------------------------------------------------
    def do_GET(self) -> None:
        if not self._check_host():
            return
        route = urlsplit(self.path).path
        if route in ("/", "/index.html"):
            self._serve_static("index.html")
        elif route == "/api/hits":
            self._serve_json(build_dashboard(self.server.data_dir))
        elif route == "/api/health":
            self._serve_json({"ok": True, "version": __version__})
        elif route == "/api/scan":
            self._serve_json(self.server.scan_task.to_dict())
        elif route == "/api/config":
            self._serve_json(load_web_config(self.server.data_dir))
        elif route.startswith("/static/"):
            self._serve_static(route[len("/static/") :])
        else:
            self._send(404, b"Not Found", "text/plain; charset=utf-8")

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        # 安全闸门：Host → Origin → Content-Type → body 大小，任一不过就短路
        if not self._check_host():
            return
        if not self._check_origin():
            return
        route = urlsplit(self.path).path
        if route not in ("/api/scan", "/api/config"):
            self._send(404, b"Not Found", "text/plain; charset=utf-8")
            return
        if not self._check_content_type():
            return
        body = self._read_body()
        if body is None:
            return
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._error_json(400, "请求体不是合法 JSON")
            return
        if route == "/api/config":
            self._handle_config_save(payload)
        else:
            self._handle_scan_start(payload)

    # -- 安全校验 ----------------------------------------------------
    def _check_host(self) -> bool:
        """防 DNS rebinding：Host 去掉端口后必须是环回主机。"""
        host = self.headers.get("Host", "")
        if _hostname_of(host) not in LOOPBACK_HOSTS:
            self._error_json(403, "仅允许本机访问（Host 校验失败）", close=True)
            return False
        return True

    def _check_origin(self) -> bool:
        """请求带 Origin 时，其必须是环回 origin，否则视为跨站请求拒绝。"""
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        parts = urlsplit(origin)
        if parts.scheme not in ("http", "https") or parts.hostname not in LOOPBACK_HOSTS:
            self._error_json(403, "跨站请求被拒绝（Origin 校验失败）", close=True)
            return False
        return True

    def _check_content_type(self) -> bool:
        """POST 必须声明 application/json，挡住 HTML 表单发起的跨站简单请求。"""
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._error_json(415, "Content-Type 必须是 application/json", close=True)
            return False
        return True

    def _read_body(self) -> bytes | None:
        """按 Content-Length 精确读取；缺失或超限一律 413，且只读到上限为止。"""
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            self._error_json(413, "缺少 Content-Length 或请求体过大", close=True)
            return None
        try:
            length = int(raw_length)
        except ValueError:
            self._error_json(413, "Content-Length 非法", close=True)
            return None
        if length < 0 or length > MAX_BODY_BYTES:
            self._error_json(413, f"请求体超过上限 {MAX_BODY_BYTES} 字节", close=True)
            return None
        return self.rfile.read(length)

    # -- 业务处理 ----------------------------------------------------
    def _handle_scan_start(self, payload: Any) -> None:
        params, error = validate_scan_params(payload)
        if error:
            self._error_json(400, error)
            return
        assert params is not None
        task = self.server.start_scan(params)
        if task is None:
            self._error_json(409, "已有扫描在运行，请稍候")
            return
        self._send_json(202, {"status": task.status, "params": task.params})

    def _handle_config_save(self, payload: Any) -> None:
        cfg, error = validate_web_config(payload)
        if error:
            self._error_json(400, error)
            return
        assert cfg is not None
        save_web_config(self.server.data_dir, cfg)
        self._send_json(200, cfg)

    # -- 输出 --------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _serve_json(self, payload: Any) -> None:
        self._send_json(200, payload)

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error_json(self, status: int, message: str, *, close: bool = False) -> None:
        if close:
            # 未读取请求体时直接关连接，避免 keep-alive 下残留 body 被当成新请求
            self.close_connection = True
        body = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _serve_static(self, name: str) -> None:
        # 白名单匹配，天然杜绝路径穿越（../）
        if name not in STATIC_TYPES:
            self._send(404, b"Not Found", "text/plain; charset=utf-8")
            return
        try:
            body = (STATIC_DIR / name).read_bytes()
        except OSError:
            self._send(500, b"Static file missing", "text/plain; charset=utf-8")
            return
        self._send(200, body, STATIC_TYPES[name])

    def log_message(self, fmt: str, *args: Any) -> None:  # 精简访问日志
        sys.stderr.write(f"[看板] {self.address_string()} {fmt % args}\n")


def _hostname_of(host: str) -> str:
    """从 Host/Origin 头里取出主机名（去掉端口与方括号）。"""
    try:
        return urlsplit("//" + host).hostname or ""
    except ValueError:
        return ""


def create_server(
    data_dir: Path | str,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    scan_runner: Callable[[Path, dict[str, Any], Callable[[str], None]], dict[str, Any]] | None = None,
) -> DashboardServer:
    """创建（但不启动）看板服务器；``port=0`` 可让系统分配空闲端口（便于测试）。

    ``scan_runner`` 可注入假实现，供测试离线驱动扫描（默认走 ``default_scan_runner``）。
    """
    return DashboardServer((host, port), data_dir, scan_runner=scan_runner)


# ---------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m newspaper_auto.web",
        description="六家报社数字报监测 · 本地看板（可配置关键词/天数并一键触发扫描，零依赖）",
    )
    parser.add_argument("--data-dir", default=None, help="数据目录（含 hits/ 与 state/），默认自动探测项目根")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"监听地址（默认 {DEFAULT_HOST}，仅本机可访问）")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"监听端口（默认 {DEFAULT_PORT}，0 表示自动分配）"
    )
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = paths.resolve_data_dir(args.data_dir)

    try:
        server = create_server(data_dir, args.host, args.port)
    except OSError as exc:
        print(f"启动失败：{args.host}:{args.port} 不可用（{exc}）。", file=sys.stderr)
        print("可换端口重试，例如 --port 8788，或用 --port 0 让系统自动分配。", file=sys.stderr)
        return 1

    host, port = server.server_address[0], server.server_address[1]
    url = f"http://{host}:{port}/"

    print(f"看板已启动：{url}")
    print(f"数据目录：{data_dir}")
    print(f"数据源：{paths.archive_file(data_dir)}")
    print("可在页面「扫描设置」中触发抓取；按 Ctrl+C 停止")

    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
