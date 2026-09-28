"""看板请求参数校验与服务端结果筛选。

纯函数集合：把 ``POST /api/scan``、``POST /api/config``、``POST /api/export``
的请求体收敛为归一化字典，并在导出时对归档记录应用与前端一致的筛选语义。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from . import config, exporter
from .web_static import (
    DATE_RE,
    EXPORT_SORTS,
    MAX_EXPORT_FILTER_ITEMS,
    MAX_EXPORT_QUERY_LEN,
    MAX_KEYWORD_LEN,
    MAX_KEYWORDS,
    MAX_WORKERS,
    SCAN_MODES,
)


def _valid_days(value: Any) -> bool:
    """天数必须是 1~``config.MAX_RANGE_DAYS`` 的整数（``bool`` 被 :func:`config.is_int` 排除）。"""
    return config.is_int(value) and 1 <= value <= config.MAX_RANGE_DAYS


def _valid_workers(value: Any) -> bool:
    """并发数必须是 1~``MAX_WORKERS`` 的整数。"""
    return config.is_int(value) and 1 <= value <= MAX_WORKERS


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
    if days is not None and not _valid_days(days):
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
    elif not _valid_workers(workers):
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
    if not _valid_workers(workers):
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
        if not _valid_days(days):
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
    if not _valid_days(days):
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
        # 完整降序表原地反转，等价于按 (date, url) 升序排 —— 两者严格互为镜像，
        # 同一天内的并列顺序不会被"额外"打乱（别改成再排一次，没有区别）。
        selected.reverse()
    elif sort == "paper":
        selected.sort(key=lambda item: item["paper"])
    elif sort == "title":
        selected.sort(key=lambda item: item["title"])
    return selected
