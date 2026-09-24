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
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import webbrowser
from collections.abc import Callable, Sequence
from copy import deepcopy
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import __version__, cli, config, exporter, papers, paths, report, scanner, state
from .web_tasks import ScanTask

STATIC_DIR = Path(__file__).parent / "static"
STATIC_TYPES = {
    "index.html": "text/html; charset=utf-8",
    "api.js": "application/javascript; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}

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


class ScanRequestError(ValueError):
    """扫描请求在进入后台线程前即已确定无效。"""


# ---------------------------------------------------------------- 数据装配
def _read_archive(path: Path) -> list[Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


_GEOMETRY_NUMERIC_KEYS = (
    "x1",
    "y1",
    "x2",
    "y2",
    "left",
    "top",
    "right",
    "bottom",
    "index",
    "total",
)


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


def _normalize_layout(raw: Any) -> dict[str, Any] | None:
    """收敛版面布局为安全的 0~1 坐标，避免脏归档撑破或逃逸前端画布。"""
    if not isinstance(raw, dict) or raw.get("basis") not in ("area", "mapping"):
        return None
    raw_items = raw.get("items")
    if not isinstance(raw_items, list):
        return None
    items: list[dict[str, Any]] = []
    for item in raw_items[:500]:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            continue
        coords = [item.get(key) for key in ("left", "top", "right", "bottom")]
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in coords):
            continue
        left, top, right, bottom = (float(value) for value in coords)
        if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
            continue
        items.append(
            {
                "url": item["url"],
                "left": round(left, 4),
                "top": round(top, 4),
                "right": round(right, 4),
                "bottom": round(bottom, 4),
            }
        )
    if not items:
        return None
    aspect_ratio = raw.get("aspect_ratio", 0.72)
    if not isinstance(aspect_ratio, (int, float)) or isinstance(aspect_ratio, bool):
        aspect_ratio = 0.72
    return {
        "basis": raw["basis"],
        "aspect_ratio": round(max(0.35, min(float(aspect_ratio), 1.5)), 4),
        "items": items,
    }


def _normalize(item: Any) -> dict[str, Any] | None:
    """把归档条目收敛成前端需要的形状；丢弃无 URL 的脏数据。"""
    if not isinstance(item, dict) or not item.get("url"):
        return None
    try:
        text_len = int(item.get("text_len") or 0)
    except (TypeError, ValueError):
        text_len = 0
    raw_snippets = item.get("snippets")
    raw_matched = item.get("matched")
    return {
        "paper": str(item.get("paper", "")),
        "date": str(item.get("date", "")),
        "section": str(item.get("section") or ""),
        "section_no": str(item.get("section_no") or ""),
        "title": str(item.get("title", "")),
        "url": str(item.get("url", "")),
        "text_len": text_len,
        "snippets": [str(s) for s in raw_snippets if s] if isinstance(raw_snippets, list) else [],
        "matched": [str(k) for k in raw_matched if k] if isinstance(raw_matched, list) else [],
        "geometry": _normalize_geometry(item.get("geometry")),
        "layout": _normalize_layout(item.get("layout")),
    }


def build_dashboard(data_dir: Path | str, now: datetime | None = None) -> dict[str, Any]:
    """组装看板所需的全部数据（纯读取，无副作用）。"""
    now = now or datetime.now()
    data_dir = Path(data_dir)
    archive_path = paths.archive_file(data_dir)

    records = [rec for rec in (_normalize(item) for item in _read_archive(archive_path)) if rec]
    records.sort(key=lambda r: (r["date"], r["paper"], r["url"]), reverse=True)

    seen = state.load_seen(paths.state_file(data_dir))
    saved_config = load_web_config(data_dir)
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
        # 空状态引导用：该数据目录是否已生成 web_config.json（判断是否全新环境），
        # 以及默认关键词（全新环境时提示用户「默认扫什么」）。
        "has_web_config": paths.web_config_file(data_dir).exists(),
        "default_keywords": list(config.DEFAULT_KEYWORDS),
        # 当前设备保存的扫描关键词。前端据此默认聚焦本轮主题，而不是把历史关键词全部混在一起。
        "configured_keywords": saved_config["keywords"],
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
        if value not in cleaned:
            cleaned.append(value)
    if len(cleaned) > MAX_KEYWORDS:
        return None, f"keywords 最多 {MAX_KEYWORDS} 个"
    return cleaned, None


def _clean_only(raw: Any) -> tuple[str | None, str | None]:
    """收敛「只扫选中报社」字段为逗号分隔字符串（空串 = 全部）。

    接受字符串，或字符串数组（逐项去空、去重后以逗号连接）。``None`` / 缺省视作
    空串（全选）。返回 ``(归一化字符串, 错误说明)``；类型非法时字符串为 None。
    """
    if raw is None:
        return "", None
    if isinstance(raw, str):
        return raw.strip(), None
    if isinstance(raw, list):
        names: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                return None, "only 的每个元素都必须是字符串"
            value = item.strip()
            if value and value not in names:
                names.append(value)
        return ",".join(names), None
    return None, "only 必须是字符串或字符串数组"


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
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        since_dt = None
        until_dt = today
        if since is not None:
            since_dt = _parse_iso_date(since)
            if since_dt is None:
                return None, "since 必须是合法的 YYYY-MM-DD 日期"
        if until is not None:
            until_dt = _parse_iso_date(until)
            if until_dt is None:
                return None, "until 必须是合法的 YYYY-MM-DD 日期"
        since_dt = since_dt or until_dt
        if since_dt > until_dt:
            return None, "since 不能晚于 until"
        if (until_dt - since_dt).days + 1 > config.MAX_RANGE_DAYS:
            return None, f"日期跨度不能超过 {config.MAX_RANGE_DAYS} 天"
        since_text = since_dt.strftime("%Y-%m-%d")
        until_text = until_dt.strftime("%Y-%m-%d")

    workers = payload.get("workers")
    if workers is None:
        workers = config.DEFAULT_WORKERS
    elif not _is_int(workers) or not 1 <= workers <= MAX_WORKERS:
        return None, f"workers 必须是 1~{MAX_WORKERS} 之间的整数"

    if not use_range and days is None:
        days = config.DEFAULT_DAYS

    only, only_error = _clean_only(payload.get("only"))
    if only_error:
        return None, only_error

    full = payload.get("full", False)
    if not isinstance(full, bool):
        return None, "full 必须是布尔值"

    return {
        "keywords": keywords,
        "days": None if use_range else days,
        "since": since_text,
        "until": until_text,
        "workers": workers,
        "only": only,
        "full": full,
    }, None


def validate_web_config(payload: Any) -> tuple[dict[str, Any] | None, str | None]:
    """校验 ``POST /api/config`` 的请求体（关键词 / 范围模式 / 并发）。

    ``mode`` 缺省视作 ``"days"``（保证旧配置文件仍可读）。``mode="days"`` 时
    ``since``/``until`` 必须为空（给了直接 400，而不是静默丢弃）；``mode="range"`` 时
    两者都必须合法、``since <= until``、跨度 ≤ ``config.MAX_RANGE_DAYS``。
    """
    if not isinstance(payload, dict):
        return None, "请求体必须是 JSON 对象"
    keywords, error = _clean_keywords(payload.get("keywords"))
    if error:
        return None, error
    workers = payload.get("workers", config.DEFAULT_WORKERS)
    if not _is_int(workers) or not 1 <= workers <= MAX_WORKERS:
        return None, f"workers 必须是 1~{MAX_WORKERS} 之间的整数"

    mode = payload.get("mode", "days")
    if mode not in SCAN_MODES:
        return None, "mode 必须是 days 或 range"

    only, only_error = _clean_only(payload.get("only"))
    if only_error:
        return None, only_error

    days = payload.get("days", config.DEFAULT_DAYS)
    since = payload.get("since")
    until = payload.get("until")

    if mode == "days":
        if since is not None or until is not None:
            return None, "mode=days 时不能提供 since/until"
        if not _is_int(days) or not 1 <= days <= config.MAX_RANGE_DAYS:
            return None, f"days 必须是 1~{config.MAX_RANGE_DAYS} 之间的整数"
        return {
            "keywords": keywords,
            "days": days,
            "workers": workers,
            "mode": "days",
            "since": None,
            "until": None,
            "only": only,
        }, None

    # mode == "range"
    since_dt = _parse_iso_date(since) if since is not None else None
    until_dt = _parse_iso_date(until) if until is not None else None
    if since_dt is None:
        return None, "mode=range 时 since 必须是合法的 YYYY-MM-DD 日期"
    if until_dt is None:
        return None, "mode=range 时 until 必须是合法的 YYYY-MM-DD 日期"
    if since_dt > until_dt:
        return None, "since 不能晚于 until"
    if (until_dt - since_dt).days + 1 > config.MAX_RANGE_DAYS:
        return None, f"日期跨度不能超过 {config.MAX_RANGE_DAYS} 天"
    if not _is_int(days) or not 1 <= days <= config.MAX_RANGE_DAYS:
        days = config.DEFAULT_DAYS
    return {
        "keywords": keywords,
        "days": days,
        "workers": workers,
        "mode": "range",
        "since": since_dt.strftime("%Y-%m-%d"),
        "until": until_dt.strftime("%Y-%m-%d"),
        "only": only,
    }, None


def load_web_config(data_dir: Path | str) -> dict[str, Any]:
    """读取看板配置；文件缺失或内容非法时回落到默认值。

    ``mode`` / ``since`` / ``until`` 缺省填 ``"days"`` / ``None`` / ``None``，
    ``only`` 缺省填 ``""``（= 全选），因此**旧版本写出的没有这些字段的配置文件仍可读**。
    """
    defaults = {
        "keywords": list(config.DEFAULT_KEYWORDS),
        "days": config.DEFAULT_DAYS,
        "workers": config.DEFAULT_WORKERS,
        "mode": "days",
        "since": None,
        "until": None,
        "only": "",
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

    mode = data.get("mode", "days")
    since_text: str | None = None
    until_text: str | None = None
    if mode == "range":
        since_dt = _parse_iso_date(data.get("since"))
        until_dt = _parse_iso_date(data.get("until"))
        if (
            since_dt is not None
            and until_dt is not None
            and since_dt <= until_dt
            and (until_dt - since_dt).days + 1 <= config.MAX_RANGE_DAYS
        ):
            since_text = since_dt.strftime("%Y-%m-%d")
            until_text = until_dt.strftime("%Y-%m-%d")
        else:
            # 旧的/损坏的 range 配置 → 安全回落到按天数
            mode = "days"
    else:
        mode = "days"

    only = ""
    raw_only = data.get("only")
    if isinstance(raw_only, str):
        only = raw_only.strip()
    elif isinstance(raw_only, list) and all(isinstance(item, str) for item in raw_only):
        only = ",".join(item.strip() for item in raw_only if item.strip())

    return {
        "keywords": keywords,
        "days": days,
        "workers": workers,
        "mode": mode,
        "since": since_text,
        "until": until_text,
        "only": only,
    }


def save_web_config(data_dir: Path | str, cfg: dict[str, Any]) -> None:
    """把配置写回 ``state/web_config.json``（自动建目录）。"""
    report.write_text(
        paths.web_config_file(Path(data_dir)),
        json.dumps(cfg, ensure_ascii=False, indent=1),
    )


# ---------------------------------------------------------------- 结果导出
def _clean_export_filter_list(raw: Any, field: str) -> tuple[list[str] | None, str | None]:
    """校验导出筛选数组；None 表示未限定，空数组明确表示不匹配任何记录。"""
    if raw is None:
        return None, None
    if not isinstance(raw, list):
        return None, f"filters.{field} 必须是字符串数组"
    if len(raw) > MAX_EXPORT_FILTER_ITEMS:
        return None, f"filters.{field} 最多 {MAX_EXPORT_FILTER_ITEMS} 项"
    cleaned: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            return None, f"filters.{field} 不能包含空值或非字符串"
        value = item.strip()
        if value not in cleaned:
            cleaned.append(value)
    return cleaned, None


def validate_export_request(
    payload: Any, *, base_dir: str | Path | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    """校验 ``POST /api/export``；不创建目录，也不接受客户端上传记录。"""
    if not isinstance(payload, dict):
        return None, "请求体必须是 JSON 对象"
    unknown = set(payload) - {"directory", "format", "filters"}
    if unknown:
        return None, f"不支持的字段：{'、'.join(sorted(unknown))}"

    try:
        directory = exporter.resolve_directory(payload.get("directory"), base_dir=base_dir, create=False)
        format_name = exporter.normalize_format(payload.get("format"))
    except exporter.ExportValidationError as exc:
        return None, str(exc)

    raw_filters = payload.get("filters", {})
    if raw_filters is None:
        raw_filters = {}
    if not isinstance(raw_filters, dict):
        return None, "filters 必须是 JSON 对象"
    unknown_filters = set(raw_filters) - {"papers", "keywords", "urls", "from", "to", "query", "sort"}
    if unknown_filters:
        return None, f"不支持的 filters 字段：{'、'.join(sorted(unknown_filters))}"

    paper_names, error = _clean_export_filter_list(raw_filters.get("papers"), "papers")
    if error:
        return None, error
    keywords, error = _clean_export_filter_list(raw_filters.get("keywords"), "keywords")
    if error:
        return None, error
    urls, error = _clean_export_filter_list(raw_filters.get("urls"), "urls")
    if error:
        return None, error

    query = raw_filters.get("query", "")
    if not isinstance(query, str):
        return None, "filters.query 必须是字符串"
    query = query.strip()
    if len(query) > MAX_EXPORT_QUERY_LEN:
        return None, f"filters.query 不能超过 {MAX_EXPORT_QUERY_LEN} 个字符"

    normalized_dates: dict[str, str | None] = {"from": None, "to": None}
    for field in ("from", "to"):
        value = raw_filters.get(field)
        if value in (None, ""):
            continue
        parsed = _parse_iso_date(value)
        if parsed is None:
            return None, f"filters.{field} 必须是合法的 YYYY-MM-DD 日期"
        normalized_dates[field] = parsed.strftime("%Y-%m-%d")
    if (
        normalized_dates["from"] is not None
        and normalized_dates["to"] is not None
        and normalized_dates["from"] > normalized_dates["to"]
    ):
        return None, "filters.from 不能晚于 filters.to"

    sort = raw_filters.get("sort", "date-desc")
    if sort not in EXPORT_SORTS:
        return None, f"filters.sort 必须是 {'、'.join(EXPORT_SORTS)} 之一"

    return {
        "directory": str(directory),
        "format": format_name,
        "filters": {
            "papers": paper_names,
            "keywords": keywords,
            "urls": urls,
            "from": normalized_dates["from"],
            "to": normalized_dates["to"],
            "query": query,
            "sort": sort,
        },
    }, None


def filter_export_records(records: list[dict[str, Any]], filters: dict[str, Any]) -> list[dict[str, Any]]:
    """在服务端对归档记录应用看板同语义筛选，避免信任前端上传的记录。"""
    paper_names = filters.get("papers")
    keywords = filters.get("keywords")
    urls = filters.get("urls")
    from_date = filters.get("from")
    to_date = filters.get("to")
    query = str(filters.get("query") or "").casefold()

    selected: list[dict[str, Any]] = []
    for record in records:
        if paper_names is not None and record["paper"] not in paper_names:
            continue
        export_record = record
        if keywords is not None:
            visible_matched = [keyword for keyword in record["matched"] if keyword in keywords]
            if not visible_matched:
                continue
            # 导出内容与当前关键词视图保持一致，但不修改原始归档。
            export_record = dict(record)
            export_record["matched"] = visible_matched
        if urls is not None and record["url"] not in urls:
            continue
        if from_date and (not record["date"] or record["date"] < from_date):
            continue
        if to_date and (not record["date"] or record["date"] > to_date):
            continue
        if query:
            haystack = " ".join((record["title"], record["section"], *record["snippets"])).casefold()
            if query not in haystack:
                continue
        selected.append(export_record)

    # 先建立统一的日期倒序基线；paper/title 排序利用 Python 稳定排序保留同组日期顺序。
    selected.sort(key=lambda item: (item["date"], item["url"]), reverse=True)
    sort = filters.get("sort")
    if sort == "date-asc":
        selected.reverse()
    elif sort == "paper":
        selected.sort(key=lambda item: item["paper"])
    elif sort == "title":
        selected.sort(key=lambda item: item["title"])
    return selected


# ---------------------------------------------------------------- 自定义报社
def build_papers_view(root: Path | str) -> dict[str, Any]:
    """组装报社目录；保留旧字段，并提供前端可直接渲染的结构化 ``items``。"""
    root = Path(root)
    settings = papers.load_settings(root)
    custom = list(settings.custom)
    disabled = set(settings.disabled)
    catalog = papers.merge_papers(config.PAPERS, custom)
    effective = papers.merge_papers(config.PAPERS, custom, disabled)
    builtin_names = {paper["name"] for paper in config.PAPERS}
    custom_names = {paper["name"] for paper in custom}
    items = []
    for paper in catalog:
        name = paper["name"]
        if name in builtin_names and name in custom_names:
            source = "override"
        elif name in custom_names:
            source = "custom"
        else:
            source = "builtin"
        items.append(
            {
                "name": name,
                "source": source,
                "enabled": name not in disabled,
                "config": dict(paper),
            }
        )
    return {
        "builtin": [paper["name"] for paper in config.PAPERS],
        "custom": custom,
        "effective": [paper["name"] for paper in effective],
        "disabled": [paper["name"] for paper in catalog if paper["name"] in disabled],
        "items": items,
    }


def _resolve_paper_by_name(root: Path | str, name: Any) -> dict[str, Any] | None:
    """按名称从「内置 + 自定义」合并结果里取出完整报社对象（供仅给 name 的试抓）。"""
    if not isinstance(name, str) or not name.strip():
        return None
    wanted = name.strip()
    settings = papers.load_settings(Path(root))
    for paper in papers.merge_papers(config.PAPERS, settings.custom):
        if paper["name"] == wanted:
            return dict(paper)
    return None


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
    """默认扫描执行器：进程内调用 ``cli.main``，并通过回调实时接收日志。

    不使用 ``redirect_stdout``：它会修改整个进程的全局流，在多线程 HTTP 服务里可能把
    访问日志卷入扫描日志。argparse 的 ``SystemExit(2)`` 仍兜住转成 ``exit_code``。
    """
    argv = build_scan_argv(data_dir, params)
    lines: list[str] = []
    exit_code = 0

    def capture(message: str = "") -> None:
        line = str(message)
        lines.append(line)
        log(line)

    try:
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
    day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
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
        paper_probe: Callable[[dict[str, Any], Callable[[str], None]], dict[str, Any]] | None = None,
        papers_root: Path | str | None = None,
    ):
        super().__init__(address, DashboardHandler)
        self.data_dir = Path(data_dir)
        # 执行器 / 探测可注入：测试用假实现，绝不碰网络
        self.scan_runner = scan_runner or default_scan_runner
        self.paper_probe = paper_probe or default_paper_probe
        # 自定义报社跟随**项目根**（配置层），与 data_dir（数据层）无关；
        # 显式传入 papers_root 仅用于测试隔离，避免污染真实项目根。
        self.papers_root = Path(papers_root) if papers_root is not None else paths.find_project_root()
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
            self.scan_lock.release()


class DashboardHandler(BaseHTTPRequestHandler):
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
        elif route == "/api/hits":
            self._serve_json(build_dashboard(self.server.data_dir))
        elif route == "/api/health":
            self._serve_json({"ok": True, "version": __version__})
        elif route == "/api/scan":
            query = parse_qs(parsed.query)
            raw_after = query.get("after", [None])[0]
            try:
                after = int(raw_after) if raw_after is not None else None
            except (TypeError, ValueError):
                self._error_json(400, "after 必须是非负整数")
                return
            if after is not None and after < 0:
                self._error_json(400, "after 必须是非负整数")
                return
            self._serve_json(self.server.scan_task.snapshot(after=after))
        elif route == "/api/config":
            self._serve_json(load_web_config(self.server.data_dir))
        elif route == "/api/papers":
            self._serve_json(build_papers_view(self.server.papers_root))
        elif route.startswith("/static/"):
            self._serve_static(route[len("/static/") :])
        else:
            self._error_json(404, "请求的页面或接口不存在")

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        # 安全闸门：Host → Origin → Content-Type → body 大小，任一不过就短路
        if not self._check_host():
            return
        if not self._check_origin():
            return
        route = urlsplit(self.path).path
        if route not in (
            "/api/scan",
            "/api/config",
            "/api/papers",
            "/api/papers/delete",
            "/api/papers/status",
            "/api/papers/test",
            "/api/export",
        ):
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
        if route == "/api/config":
            self._handle_config_save(payload)
        elif route == "/api/scan":
            self._handle_scan_start(payload)
        elif route == "/api/papers":
            self._handle_paper_add(payload)
        elif route == "/api/papers/delete":
            self._handle_paper_delete(payload)
        elif route == "/api/papers/status":
            self._handle_paper_status(payload)
        elif route == "/api/papers/test":
            self._handle_paper_test(payload)
        else:
            self._handle_export(payload)

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

    # -- 业务处理 ----------------------------------------------------
    def _handle_scan_start(self, payload: Any) -> None:
        params, error = validate_scan_params(payload)
        if error:
            self._error_json(400, error)
            return
        assert params is not None
        selection_error = self.server.paper_selection_error(params["only"])
        if selection_error:
            self._error_json(400, selection_error)
            return
        try:
            task = self.server.start_scan(params)
        except ScanRequestError as exc:
            self._error_json(400, str(exc))
            return
        except Exception as exc:
            self._error_json(503, f"扫描任务启动失败：{exc}")
            return
        if task is None:
            self._error_json(409, "已有扫描在运行，请稍候")
            return
        self._send_json(202, task.snapshot(after=0))

    def _handle_config_save(self, payload: Any) -> None:
        cfg, error = validate_web_config(payload)
        if error:
            self._error_json(400, error)
            return
        assert cfg is not None
        selection_error = self.server.paper_selection_error(cfg["only"])
        if selection_error:
            self._error_json(400, selection_error)
            return
        try:
            with self.server.config_lock:
                save_web_config(self.server.data_dir, cfg)
        except OSError as exc:
            self._error_json(500, f"配置保存失败：{exc}")
            return
        self._send_json(200, cfg)

    def _handle_export(self, payload: Any) -> None:
        """把服务端归档的受控筛选结果原子写入用户指定文件夹。"""
        request, error = validate_export_request(payload, base_dir=self.server.data_dir)
        if error:
            self._error_json(400, error)
            return
        assert request is not None

        dashboard = build_dashboard(self.server.data_dir)
        filters = request["filters"]
        requested_papers = filters["papers"]
        if requested_papers is not None:
            available = set(dashboard["papers"])
            unknown = [value for value in requested_papers if value not in available]
            if unknown:
                self._error_json(400, f"filters.papers 包含未知值：{'、'.join(unknown)}")
                return
        requested_urls = filters["urls"]
        if requested_urls is not None:
            available_urls = {record["url"] for record in dashboard["records"]}
            unknown_urls = [value for value in requested_urls if value not in available_urls]
            if unknown_urls:
                self._error_json(400, "filters.urls 包含未知或已失效的报道链接")
                return

        records = filter_export_records(dashboard["records"], filters)
        try:
            output_path = exporter.export_hits(request["directory"], request["format"], records)
        except exporter.ExportValidationError as exc:
            self._error_json(400, str(exc))
            return
        except OSError as exc:
            self._error_json(500, f"导出失败：{exc}")
            return
        self._send_json(
            201,
            {"path": str(output_path), "count": len(records), "format": request["format"]},
        )

    def _handle_paper_add(self, payload: Any) -> None:
        """新增或**同名覆盖**一条自定义报社，写回后返回最新列表。"""
        paper, error = papers.validate_paper(payload)
        if error:
            self._error_json(400, error)
            return
        assert paper is not None
        root = self.server.papers_root
        if self.server.scan_lock.locked():
            self._error_json(409, "扫描进行中，完成后才能修改报社配置")
            return
        try:
            with self.server.papers_lock:
                if self.server.scan_lock.locked():
                    self._error_json(409, "扫描进行中，完成后才能修改报社配置")
                    return
                settings = papers.load_settings_for_update(root)
                current = list(settings.custom)
                updated: list[dict[str, Any]] = []
                replaced = False
                for existing in current:
                    if existing["name"] == paper["name"]:
                        updated.append(paper)  # 原地替换，保持文件内顺序
                        replaced = True
                    else:
                        updated.append(existing)
                if not replaced:
                    updated.append(paper)
                known_before = {item["name"] for item in config.PAPERS} | {item["name"] for item in current}
                disabled = (
                    settings.disabled
                    if paper["name"] in known_before
                    else tuple(item for item in settings.disabled if item != paper["name"])
                )
                papers.save_settings(root, updated, disabled)
        except papers.CustomPapersError as exc:
            self._error_json(409, f"报社配置未保存：{exc}。请先修复原文件")
            return
        except OSError as exc:
            self._error_json(500, f"报社配置保存失败：{exc}")
            return
        self._send_json(200, build_papers_view(root))

    def _handle_paper_delete(self, payload: Any) -> None:
        """删除一条自定义报社；内置且未被覆盖的不可删（400），不存在的报 404。"""
        if not isinstance(payload, dict):
            self._error_json(400, "请求体必须是 JSON 对象")
            return
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            self._error_json(400, "name 必须是非空字符串")
            return
        name = name.strip()
        root = self.server.papers_root
        if self.server.scan_lock.locked():
            self._error_json(409, "扫描进行中，完成后才能修改报社配置")
            return
        try:
            with self.server.papers_lock:
                if self.server.scan_lock.locked():
                    self._error_json(409, "扫描进行中，完成后才能修改报社配置")
                    return
                settings = papers.load_settings_for_update(root)
                current = list(settings.custom)
                if all(existing["name"] != name for existing in current):
                    builtin_names = {paper["name"] for paper in config.PAPERS}
                    if name in builtin_names:
                        self._error_json(400, "内置报社不可删除（可在自定义中同名覆盖其配置）")
                    else:
                        self._error_json(404, f"未找到报社「{name}」")
                    return
                builtin_names = {paper["name"] for paper in config.PAPERS}
                disabled = (
                    settings.disabled
                    if name in builtin_names
                    else tuple(item for item in settings.disabled if item != name)
                )
                papers.save_settings(root, [p for p in current if p["name"] != name], disabled)
        except papers.CustomPapersError as exc:
            self._error_json(409, f"报社配置未删除：{exc}。请先修复原文件")
            return
        except OSError as exc:
            self._error_json(500, f"报社配置删除失败：{exc}")
            return
        self._send_json(200, build_papers_view(root))

    def _handle_paper_status(self, payload: Any) -> None:
        """显式启用/停用报社；操作幂等且不会删除历史结果或抓取规则。"""
        if not isinstance(payload, dict):
            self._error_json(400, "请求体必须是 JSON 对象")
            return
        unknown = set(payload) - {"name", "enabled"}
        if unknown:
            self._error_json(400, f"不支持的字段：{'、'.join(sorted(unknown))}")
            return
        name = payload.get("name")
        enabled = payload.get("enabled")
        if not isinstance(name, str) or not name.strip():
            self._error_json(400, "name 必须是非空字符串")
            return
        if not isinstance(enabled, bool):
            self._error_json(400, "enabled 必须是布尔值")
            return
        name = name.strip()
        root = self.server.papers_root
        if self.server.scan_lock.locked():
            self._error_json(409, "扫描进行中，完成后才能修改报社配置")
            return
        try:
            with self.server.papers_lock:
                if self.server.scan_lock.locked():
                    self._error_json(409, "扫描进行中，完成后才能修改报社配置")
                    return
                settings = papers.load_settings_for_update(root)
                catalog = papers.merge_papers(config.PAPERS, settings.custom)
                if name not in {paper["name"] for paper in catalog}:
                    self._error_json(404, f"未找到报社「{name}」")
                    return
                disabled = list(settings.disabled)
                if enabled:
                    disabled = [item for item in disabled if item != name]
                elif name not in disabled:
                    disabled.append(name)
                papers.save_settings(root, settings.custom, disabled)
        except papers.CustomPapersError as exc:
            self._error_json(409, f"报社状态未保存：{exc}。请先修复原文件")
            return
        except OSError as exc:
            self._error_json(500, f"报社状态保存失败：{exc}")
            return
        self._send_json(200, build_papers_view(root))

    def _handle_paper_test(self, payload: Any) -> None:
        """试抓探测：body 为完整 paper 对象；只给 ``name`` 时按名称解析内置/自定义。"""
        paper, error = papers.validate_paper(payload)
        if error:
            name = payload.get("name") if isinstance(payload, dict) and set(payload) == {"name"} else None
            paper = _resolve_paper_by_name(self.server.papers_root, name)
            if paper is None:
                self._error_json(400, error)
                return
        assert paper is not None
        try:
            result = self.server.paper_probe(paper, lambda line: sys.stderr.write(f"[试抓] {line}\n"))
        except Exception as exc:  # 兜底：探测异常不该让看板工作线程炸掉
            self._error_json(500, f"试抓失败：{exc}")
            return
        try:
            entry_code = int(result.get("entry_code") or 0)
            sections = int(result.get("sections") or 0)
            sample_articles = (
                int(result["sample_articles"]) if result.get("sample_articles") is not None else None
            )
        except (TypeError, ValueError):
            entry_code, sections, sample_articles = 0, 0, None
        note = str(result.get("note") or "")
        self._send_json(
            200,
            {
                "entry": str(result.get("entry") or ""),
                "entry_code": entry_code,
                "sections": sections,
                "sample_articles": sample_articles,
                "ok": entry_code == 200 and (sample_articles is None or sample_articles > 0),
                "note": note,
            },
        )

    # -- 输出 --------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
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
        try:
            body = (STATIC_DIR / name).read_bytes()
        except OSError:
            self._error_json(500, "静态资源读取失败")
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
    if host == "localhost":
        host = DEFAULT_HOST
    return DashboardServer(
        (host, port),
        data_dir,
        scan_runner=scan_runner,
        paper_probe=paper_probe,
        papers_root=papers_root,
    )


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
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = paths.resolve_data_dir(args.data_dir)

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
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
