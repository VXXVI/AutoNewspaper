"""看板静态资源、请求限额与协议无关的纯辅助函数。

集中放置静态资源白名单、环回主机、各类上限常量，以及 gzip / ETag 解析等
不依赖 HTTP 处理器的工具，供拆分后的各 ``web_*`` 模块复用。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from . import config


def _static_dir(source_file: str | Path | None = None) -> Path:
    """静态资源目录：源码运行取 ``static/``；PyInstaller 冻结后取解包目录。

    ``source_file`` 缺省取本模块 ``__file__``；主模块传入自己的 ``__file__``，
    以便测试通过 monkeypatch ``newspaper_auto.web.__file__`` 模拟冻结回退。
    """
    here = Path(source_file if source_file is not None else __file__).resolve().parent / "static"
    if here.is_dir():
        return here
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidate = Path(bundle) / "newspaper_auto" / "static"
        if candidate.is_dir():
            return candidate
    return here


STATIC_DIR = _static_dir()
STATIC_TYPES = {
    "index.html": "text/html; charset=utf-8",
    # 品牌图标：ico 是按尺寸混编的多尺寸图标，svg 供看板与文档引用
    "favicon.ico": "image/x-icon",
    "apple-touch-icon.png": "image/png",
    "logo.svg": "image/svg+xml",
    "logo-small.svg": "image/svg+xml",
    "api.js": "application/javascript; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
    "dom.js": "application/javascript; charset=utf-8",
    "features.js": "application/javascript; charset=utf-8",
    "format.js": "application/javascript; charset=utf-8",
    "render.js": "application/javascript; charset=utf-8",
    "store.js": "application/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}
# ⚠️ 白名单必须与 static/ 下实际提供的文件同步：漏一项浏览器就拿到 404。
# app.js 是 ES Module 入口，任一静态 import 失败都会让整个看板不执行（v0.3.0 曾漏 store.js）。
# 由 tests/test_web.py 的 test_all_static_assets_reachable_over_http / test_app_js_imports_are_served 守护。
# 小于该体积的静态资源不压缩（gzip 头开销不划算）
STATIC_MIN_GZIP_BYTES = 1024


def _accepts_gzip(header: str) -> bool:
    """解析 ``Accept-Encoding``，判断客户端是否接受 gzip（尊重 ``q=0``）。"""
    for part in header.split(","):
        token, _, params = part.partition(";")
        if token.strip().lower() != "gzip":
            continue
        quality = 1.0
        for param in params.split(";"):
            name, _, value = param.partition("=")
            if name.strip().lower() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 1.0
        return quality > 0
    return False


def _normalize_etag(value: str) -> str:
    """去掉 ETag 两端空白与弱比较前缀 ``W/``。"""
    value = value.strip()
    return value[2:] if value.startswith("W/") else value


def _etag_matches(header: str | None, etag: str) -> bool:
    """按 RFC 9110 弱比较语义判断 ``If-None-Match`` 是否命中当前 ETag。

    兼容逗号分隔的候选列表与通配 ``*``，并忽略两端的 ``W/`` 前缀。
    """
    if not header:
        return False
    if header.strip() == "*":
        return True
    target = _normalize_etag(etag)
    return any(_normalize_etag(candidate) == target for candidate in header.split(","))


DEFAULT_HOST = "127.0.0.1"
# 默认 8788（8787 常被本机其他开发服务占用；8788~8791 已确认空闲）。
DEFAULT_PORT = 8788

# 允许的 Host / Origin 主机名（环回）。看板只在本机使用，绝不接受外部来源。
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
# POST 请求体上限：扫描配置本身极小，给足冗余但绝不无限读。
MAX_BODY_BYTES = 16384
# 扫描日志最多保留的行数（超出丢最旧的）
MAX_LOG_LINES = 300
MAX_KEYWORDS = config.MAX_KEYWORDS
MAX_KEYWORD_LEN = config.MAX_KEYWORD_LEN
MAX_WORKERS = config.MAX_WORKERS
MAX_EXPORT_QUERY_LEN = 200
MAX_EXPORT_FILTER_ITEMS = 100
EXPORT_SORTS = ("date-desc", "date-asc", "paper", "title")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 扫描范围模式：按天数 / 按日期范围（互斥，缺省按天数以兼容旧配置）
SCAN_MODES = ("days", "range")
# SSE 进度推送的轮询间隔（秒）：只有真正变化时才写事件
SCAN_EVENT_INTERVAL = 0.4
# 看板保留的最近任务摘要条数
MAX_TASK_HISTORY = 20
