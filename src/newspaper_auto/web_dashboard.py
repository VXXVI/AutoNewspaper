"""看板数据装配：归档归一化、缓存与配置文件读写。

``build_dashboard`` 把 ``hits/archive.json``、``state/seen.json``、
``state/web_config.json`` 组装成前端一次渲染所需的完整字典；归档 / 增量 / 配置
的 (mtime_ns, size) 未变时命中内存缓存。缓存中的对象绝不外泄，返回值始终是深拷贝。
"""

from __future__ import annotations

import json
import threading
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__, config, paths, report, state
from .web_static import MAX_KEYWORDS
from .web_validate import _parse_iso_date, _valid_days, _valid_workers


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


LAYOUT_KEY_SEP = "\u0001"


def layout_key(record: dict[str, Any]) -> str:
    """版面分组的键：取值口径必须与前端 ``render.js`` 的 ``layoutKeyOf`` 完全一致。

    归档会把同一版面的 ``layout`` 写到该版**每一条**记录上。若逐条下发，一个 40 篇文章
    的版面就会把同一份坐标重复 40 次（实测可占响应体的 90%）。``build_dashboard`` 因此把
    layout 抽到顶层按本键去重，只发一份。
    """
    return LAYOUT_KEY_SEP.join(
        [
            str(record.get("paper") or ""),
            str(record.get("date") or ""),
            str(record.get("section_no") or ""),
            str(record.get("section") or "") or "—",
        ]
    )


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


def _file_stamp(path: Path) -> tuple[int, int]:
    """(mtime_ns, size)；文件不存在记 (0, 0)。用于缓存失效判定。"""
    try:
        stat = path.stat()
    except OSError:
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


# 看板装配缓存：key=数据目录，value=(文件指纹, 装配结果)。
# 归档/增量/配置未变化时直接复用，避免每次请求都全量 parse + sort。
_DASHBOARD_CACHE: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}
_DASHBOARD_CACHE_LOCK = threading.Lock()


def build_dashboard(data_dir: Path | str, now: datetime | None = None) -> dict[str, Any]:
    """组装看板所需的全部数据（纯读取，无副作用）。

    归档 / ``seen.json`` / ``web_config.json`` 的 (mtime_ns, size) 未变时命中内存缓存，
    省去全量 JSON 解析与排序；显式传入 ``now``（测试用）时跳过缓存，保证 ``generated_at`` 可控。
    为避免调用方改动缓存对象污染后续响应，命中缓存时返回深拷贝。
    """
    data_dir = Path(data_dir)
    archive_path = paths.archive_file(data_dir)
    cacheable = now is None
    cache_key = str(data_dir)
    stamp: tuple[Any, ...] = (
        _file_stamp(archive_path),
        _file_stamp(paths.state_file(data_dir)),
        _file_stamp(paths.web_config_file(data_dir)),
    )
    if cacheable:
        with _DASHBOARD_CACHE_LOCK:
            cached = _DASHBOARD_CACHE.get(cache_key)
            if cached is not None and cached[0] == stamp:
                return deepcopy(cached[1])

    now = now or datetime.now()
    records = [rec for rec in (_normalize(item) for item in _read_archive(archive_path)) if rec]
    # layout 抽到顶层按版面去重：同一版面所有文章共享一份坐标，逐条下发会把响应体撑大数十倍。
    layouts: dict[str, dict[str, Any]] = {}
    for rec in records:
        layout = rec.pop("layout", None)
        if not layout:
            continue
        key = layout_key(rec)
        current = layouts.get(key)
        # 与前端原有语义一致：同一版面出现多份 layout 时保留项数最多的那份。
        if current is None or len(layout["items"]) > len(current["items"]):
            layouts[key] = layout
    records.sort(key=lambda r: (r["date"], r["paper"], r["url"]), reverse=True)

    seen = state.load_seen(paths.state_file(data_dir))
    saved_config = load_web_config(data_dir)
    seen_counts = {kw: len(bucket) for kw, bucket in seen.items() if isinstance(bucket, dict)}

    date_counts: dict[str, int] = {}
    paper_counts: dict[str, int] = {}
    for rec in records:
        date_counts[rec["date"]] = date_counts.get(rec["date"], 0) + 1
        paper_counts[rec["paper"]] = paper_counts.get(rec["paper"], 0) + 1

    result = {
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
        # 版面布局按 layout_key 去重后的共享表；记录里不再重复携带 layout。
        "layouts": layouts,
        # 空状态引导用：该数据目录是否已生成 web_config.json（判断是否全新环境），
        # 以及默认关键词（全新环境时提示用户「默认扫什么」）。
        "has_web_config": paths.web_config_file(data_dir).exists(),
        "default_keywords": list(config.DEFAULT_KEYWORDS),
        # 当前设备保存的扫描关键词。前端据此默认聚焦本轮主题，而不是把历史关键词全部混在一起。
        "configured_keywords": saved_config["keywords"],
    }
    if cacheable:
        with _DASHBOARD_CACHE_LOCK:
            _DASHBOARD_CACHE[cache_key] = (stamp, result)
        # 缓存对象绝不外泄：返回深拷贝，保证调用方改动不会污染后续响应。
        return deepcopy(result)
    return result


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
    if not _valid_days(days):
        days = defaults["days"]

    workers = data.get("workers")
    if not _valid_workers(workers):
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
