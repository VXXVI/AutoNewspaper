"""看板 HTTP 服务器：环回绑定、路由分发、安全闸门与静态/SSE 输出。

路由集中为 ``_GET_ROUTES`` / ``_POST_ROUTES`` 两张表，未知路径统一 404，
避免「白名单与 if/elif 各写一份」导致的隐性漂移。业务处理在
:mod:`newspaper_auto.web_handlers` 中通过 mixin 注入。
"""

from __future__ import annotations

import contextlib
import gzip
import json
import sys
import threading
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from . import __version__, config, papers, paths
from .web_handlers import _ApiHandlersMixin
from .web_scan import (
    _SCAN_CANCEL,
    ScanRequestError,
    _system_exit_code,
    default_paper_probe,
    default_scan_runner,
    record_task_history,
)
from .web_static import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    LOOPBACK_HOSTS,
    MAX_BODY_BYTES,
    MAX_LOG_LINES,
    STATIC_DIR,
    STATIC_MIN_GZIP_BYTES,
    STATIC_TYPES,
    _accepts_gzip,
    _etag_matches,
)
from .web_tasks import ScanTask
from .web_validate import _parse_iso_date

# 唯一的路由表：path -> 处理方法名。POST 未命中即 404（close），GET 未命中即普通 404。
_POST_ROUTES: dict[str, str] = {
    "/api/config": "_handle_config_save",
    "/api/scan": "_handle_scan_start",
    "/api/scan/cancel": "_handle_scan_cancel",
    "/api/papers": "_handle_paper_add",
    "/api/papers/delete": "_handle_paper_delete",
    "/api/papers/status": "_handle_paper_status",
    "/api/papers/test": "_handle_paper_test",
    "/api/export": "_handle_export",
}
_GET_ROUTES: dict[str, str] = {
    "/api/hits": "_serve_hits",
    "/api/health": "_serve_health",
    "/api/scan/events": "_serve_scan_events",
    "/api/scan/history": "_serve_scan_history",
    "/api/config": "_serve_config",
    "/api/papers": "_serve_papers",
}


class DashboardServer(ThreadingHTTPServer):
    """把数据目录、扫描执行器与任务状态挂在 server 上。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        data_dir: Path | str,
        scan_runner: Callable[[Path, dict[str, Any], Callable[[str], None]], dict[str, Any]] | None = None,
        paper_probe: Callable[[dict[str, Any], Callable[[str], None]], dict[str, Any]] | None = None,
        papers_root: Path | str | None = None,
    ):
        # 直接构造也可用：与 create_server 一样只允许环回，方言（localhost/::1）归一到 IPv4。
        host = address[0]
        if host not in LOOPBACK_HOSTS:
            raise ValueError("看板没有登录鉴权，只允许监听 127.0.0.1 或 localhost")
        if host in ("localhost", "::1"):
            host = DEFAULT_HOST
        super().__init__((host, address[1]), DashboardHandler)
        self.data_dir = Path(data_dir)
        # 执行器 / 探测可注入：测试用假实现，绝不碰网络
        self.scan_runner = scan_runner or default_scan_runner
        self.paper_probe = paper_probe or default_paper_probe
        # 自定义报社跟随**项目根**（配置层），与 data_dir（数据层）无关；
        # 显式传入 papers_root 仅用于测试隔离，避免污染真实项目根。
        self.papers_root = Path(papers_root) if papers_root is not None else paths.app_dir()
        self.scan_lock = threading.Lock()
        self.config_lock = threading.Lock()
        self.papers_lock = threading.Lock()
        self.scan_task = ScanTask(max_log_lines=MAX_LOG_LINES)

    def scan_units(self, params: dict[str, Any]) -> int:
        """计算预计的“报社 × 日期”工作单元数，供前端显示真实总体进度。"""
        with self.papers_lock:
            settings = papers.load_settings(self.papers_root)
            effective = papers.merge_papers(config.PAPERS, settings.custom, settings.disabled)
        selected = {name.strip() for name in str(params.get("only") or "").split(",") if name.strip()}
        paper_count = sum(1 for paper in effective if not selected or paper["name"] in selected)
        if params.get("since") and params.get("until"):
            since = _parse_iso_date(params["since"])
            until = _parse_iso_date(params["until"])
            day_count = (until - since).days + 1 if since is not None and until is not None else 0
        else:
            day_count = int(params.get("days") or config.DEFAULT_DAYS)
        return paper_count * day_count

    def paper_selection_error(self, only: str) -> str | None:
        """在启动线程前验证报社名，避免 CLI 启动后才以退出码 2 失败。"""
        wanted = [name.strip() for name in only.split(",") if name.strip()]
        with self.papers_lock:
            settings = papers.load_settings(self.papers_root)
            known = {
                paper["name"]
                for paper in papers.merge_papers(config.PAPERS, settings.custom, settings.disabled)
            }
        if not known:
            return "没有已启用的报社，请先在“报社与抓取规则”中恢复或添加报社"
        if not wanted:
            return None
        unknown = [name for name in wanted if name not in known]
        if unknown:
            return f"未找到报社：{'、'.join(unknown)}，请刷新报社列表后重试"
        return None

    def start_scan(self, params: dict[str, Any]) -> ScanTask | None:
        """尝试占用任务锁；成功则起后台线程，失败（已有任务）返回 None，绝不排队等待。"""
        if not self.scan_lock.acquire(blocking=False):
            return None
        task: ScanTask | None = None
        try:
            # HTTP 层的预检用于快速返回；这里在占有 scan_lock 后再校验一次，
            # 封住“预检完成后、线程启动前报社配置被另一请求修改”的窗口。
            selection_error = self.paper_selection_error(str(params.get("only") or ""))
            if selection_error:
                raise ScanRequestError(selection_error)
            task = ScanTask.running(
                params,
                total_units=self.scan_units(params),
                max_log_lines=MAX_LOG_LINES,
            )
            self.scan_task = task
            threading.Thread(
                target=self._run_scan,
                args=(task, deepcopy(params)),
                name=f"newspaper-scan-{task.task_id}",
                daemon=True,
            ).start()
            return task
        except Exception as exc:
            if task is not None:
                task.fail(f"扫描线程启动失败：{exc}")
            self.scan_lock.release()
            raise

    def _run_scan(self, task: ScanTask, params: dict[str, Any]) -> None:
        def log(line: str) -> None:
            task.append_log(line)
            sys.stderr.write(f"[扫描] {line}\n")

        token = _SCAN_CANCEL.set(task.is_cancelled)
        try:
            result = self.scan_runner(self.data_dir, deepcopy(params), log)
            task.finish(result)
        except SystemExit as exc:  # 注入的 runner 也可能抛 SystemExit
            exit_code = _system_exit_code(exc.code)
            if exit_code == 0:
                task.finish({"exit_code": 0, "summary": None})
            else:
                task.fail(f"扫描进程提前退出（退出码 {exit_code}）", exit_code=exit_code)
        except Exception as exc:  # 兜底：runner 抛错也必须释放任务锁
            task.fail(exc)
        finally:
            _SCAN_CANCEL.reset(token)
            # 先释放锁再落历史：任务一旦进入终态，下一次扫描就应能立即开始，
            # 不能让一次磁盘写入把「能不能再扫」的窗口拉长。
            self.scan_lock.release()
            with contextlib.suppress(Exception):
                record_task_history(self.data_dir, task)


class DashboardHandler(_ApiHandlersMixin, BaseHTTPRequestHandler):
    server_version = f"NewsPaperAuto/{__version__}"
    protocol_version = "HTTP/1.1"

    # -- 路由 --------------------------------------------------------
    def do_GET(self) -> None:
        if not self._check_host():
            return
        parsed = urlsplit(self.path)
        route = parsed.path
        if route in ("/", "/index.html"):
            self._serve_static("index.html")
        elif route == "/api/scan":
            self._serve_scan_status(parsed.query)
        elif route.startswith("/static/"):
            self._serve_static(route[len("/static/") :])
        else:
            handler = _GET_ROUTES.get(route)
            if handler is None:
                self._error_json(404, "请求的页面或接口不存在")
            else:
                getattr(self, handler)()

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        # 安全闸门：Host → Origin → 路由 → Content-Type → body 大小，任一不过就短路
        if not self._check_host():
            return
        if not self._check_origin():
            return
        route = urlsplit(self.path).path
        handler = _POST_ROUTES.get(route)
        if handler is None:
            # 请求体尚未读取，HTTP/1.1 keep-alive 下必须关闭连接，避免残留字节
            # 被服务器误当成下一条请求。
            self._error_json(404, "请求的接口不存在", close=True)
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
        getattr(self, handler)(payload)

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
        """按 Content-Length 精确读取，并给出符合 HTTP 语义的错误码。"""
        raw_length = self.headers.get("Content-Length")
        if raw_length is not None and self.headers.get("Transfer-Encoding") is not None:
            # 两种定长/分块机制同时出现会产生请求走私歧义，直接拒绝。
            self._error_json(400, "不能同时使用 Transfer-Encoding 与 Content-Length", close=True)
            return None
        if raw_length is None:
            self._error_json(411, "缺少 Content-Length", close=True)
            return None
        try:
            length = int(raw_length)
        except ValueError:
            self._error_json(400, "Content-Length 非法", close=True)
            return None
        if length < 0:
            self._error_json(400, "Content-Length 不能为负数", close=True)
            return None
        if length > MAX_BODY_BYTES:
            self._error_json(413, f"请求体超过上限 {MAX_BODY_BYTES} 字节", close=True)
            return None
        body = self.rfile.read(length)
        if len(body) != length:
            self._error_json(400, "请求体长度与 Content-Length 不一致", close=True)
            return None
        return body

    # -- 输出 --------------------------------------------------------
    def _send_security_headers(self) -> None:
        """所有响应（含 304）共用的安全头。"""
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; connect-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "base-uri 'none'; form-action 'none'; object-src 'none'; frame-ancestors 'none'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), geolocation=(), microphone=()")

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        extra: dict[str, str] | None = None,
        *,
        cache_control: str = "no-store",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._send_security_headers()
        self.send_header("Cache-Control", cache_control)
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

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """把未实现方法等标准库错误也收敛成带安全头的 JSON。"""
        del explain
        fallback = self.responses.get(code, ("请求失败", ""))[0]
        self._error_json(code, message or fallback, close=True)

    def _serve_static(self, name: str) -> None:
        # 白名单匹配，天然杜绝路径穿越（../）
        if name not in STATIC_TYPES:
            self._error_json(404, "静态资源不存在")
            return
        path = STATIC_DIR / name
        try:
            stat = path.stat()
            body = path.read_bytes()
        except OSError:
            self._error_json(500, "静态资源读取失败")
            return
        # ETag 由 mtime_ns + size 派生：同一秒内的改写也能让 ETag 变化，命中则回 304。
        etag = f'W/"{stat.st_mtime_ns}-{stat.st_size}"'
        gzip_eligible = len(body) >= STATIC_MIN_GZIP_BYTES
        if _etag_matches(self.headers.get("If-None-Match"), etag):
            self._send_not_modified(etag, gzip_eligible=gzip_eligible)
            return
        headers = {"ETag": etag, "Last-Modified": self.date_time_string(stat.st_mtime)}
        if gzip_eligible and _accepts_gzip(self.headers.get("Accept-Encoding", "")):
            body = gzip.compress(body, 6)
            headers["Content-Encoding"] = "gzip"
            headers["Vary"] = "Accept-Encoding"
        self._send(200, body, STATIC_TYPES[name], extra=headers, cache_control="no-cache")

    def _send_not_modified(self, etag: str, *, gzip_eligible: bool = False) -> None:
        self.send_response(304)
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "no-cache")
        self._send_security_headers()
        if gzip_eligible:
            self.send_header("Vary", "Accept-Encoding")
        self.end_headers()

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
    paper_probe: Callable[[dict[str, Any], Callable[[str], None]], dict[str, Any]] | None = None,
    papers_root: Path | str | None = None,
) -> DashboardServer:
    """创建（但不启动）看板服务器；``port=0`` 可让系统分配空闲端口（便于测试）。

    ``scan_runner`` / ``paper_probe`` 可注入假实现，供测试离线驱动扫描与试抓
    （默认分别走 ``default_scan_runner`` / ``default_paper_probe``）；
    ``papers_root`` 可注入自定义报社的文件根，供测试避免污染真实项目根。
    """
    if host not in LOOPBACK_HOSTS:
        raise ValueError("看板没有登录鉴权，只允许监听 127.0.0.1 或 localhost")
    # 不依赖本机 hosts / DNS 对 localhost 的解析结果，始终绑定明确的环回字面量。
    # ``::1`` 一并归一到 IPv4 环回：服务器以 AF_INET 绑定，直接传 "::1" 会在 bind 阶段
    # 失败并给出「端口不可用」的误导提示（v0.3.1 起归一处理）。
    if host in ("localhost", "::1"):
        host = DEFAULT_HOST
    return DashboardServer(
        (host, port),
        data_dir,
        scan_runner=scan_runner,
        paper_probe=paper_probe,
        papers_root=papers_root,
    )
