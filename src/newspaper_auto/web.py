"""本地只读看板 —— 用标准库起的单页 Web 界面（零第三方依赖）。

用法::

    python3 -m newspaper_auto.web                    # http://127.0.0.1:8787
    python3 -m newspaper_auto.web --port 9000 --open
    python3 -m newspaper_auto.web --data-dir /path/to/data

特点：
- **只读**：仅读取 ``hits/archive.json`` 与 ``state/seen.json``，不做任何写入、不触发抓取。
- **默认只监听 127.0.0.1**，不对外网暴露。
- 前端为原生 HTML/CSS/JS，无构建、无 npm、无 CDN。
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from collections.abc import Sequence
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import __version__, paths, state

STATIC_DIR = Path(__file__).parent / "static"
STATIC_TYPES = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787


# ---------------------------------------------------------------- 数据装配
def _read_archive(path: Path) -> list[Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


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
        "title": str(item.get("title", "")),
        "url": str(item.get("url", "")),
        "text_len": text_len,
        "snippets": [str(s) for s in (item.get("snippets") or []) if s],
        "matched": [str(k) for k in (item.get("matched") or []) if k],
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


# ---------------------------------------------------------------- HTTP
class DashboardServer(ThreadingHTTPServer):
    """把数据目录挂在 server 上，避免为每个请求重建配置。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], data_dir: Path | str):
        super().__init__(address, DashboardHandler)
        self.data_dir = Path(data_dir)


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = f"NewsPaperAuto/{__version__}"
    protocol_version = "HTTP/1.1"

    # -- 路由 --------------------------------------------------------
    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route in ("/", "/index.html"):
            self._serve_static("index.html")
        elif route == "/api/hits":
            self._serve_json(build_dashboard(self.server.data_dir))
        elif route == "/api/health":
            self._serve_json({"ok": True, "version": __version__})
        elif route.startswith("/static/"):
            self._serve_static(route[len("/static/") :])
        else:
            self._send(404, b"Not Found", "text/plain; charset=utf-8")

    def do_HEAD(self) -> None:
        self.do_GET()

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
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(200, body, "application/json; charset=utf-8")

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


def create_server(
    data_dir: Path | str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT
) -> DashboardServer:
    """创建（但不启动）看板服务器；``port=0`` 可让系统分配空闲端口（便于测试）。"""
    return DashboardServer((host, port), data_dir)


# ---------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m newspaper_auto.web",
        description="五家报社数字报监测 · 本地只读看板（零依赖）",
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
    print("按 Ctrl+C 停止")

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
