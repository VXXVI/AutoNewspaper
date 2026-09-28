"""增量状态（``state/seen.json``）读写与判定。

结构::

    {"关键词": {"文章URL": 1, ...}, ...}

只记录**命中过**的文章 URL。判定「新增」时按 URL 是否已出现为准（存在即已报过），
与具体是标题命中还是正文命中无关。
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from contextlib import contextmanager, suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from . import config, paths
from .fsutil import write_text

# 文章 URL 内含日期目录，如 /2026-09/22/ 或 /202609/22/；日可能缺失（形如仅 /2026-09/）
_URL_DATE_RE = re.compile(r"/(\d{4})-?(\d{2})(?:/(\d{2}))?/")

# 模块级类型别名在「导入时」求值（``from __future__ import annotations`` 只管注解），
# 因此写成字符串常量：Python 3.8 也会 TypeError（Win7 版 exe 用 3.8 构建），3.9+ 同样
# 可用。别名只在注解里使用，字符串形式对类型检查器有效。
SeenMap = "dict[str, dict[str, int]]"


def load_seen(path: str | os.PathLike[str]) -> SeenMap:
    """读取并归一化增量状态；文件缺失或局部损坏时安全降级。"""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    normalized: SeenMap = {}
    for keyword, bucket in data.items():
        if not isinstance(keyword, str) or not keyword or not isinstance(bucket, dict):
            continue
        urls = {url: 1 for url in bucket if isinstance(url, str) and url}
        if urls:
            normalized[keyword] = urls
    return normalized


def save_seen(path: str | os.PathLike[str], seen: SeenMap) -> None:
    """原子写入增量状态。"""
    write_text(path, json.dumps(seen, ensure_ascii=False, indent=1))


@contextmanager
def exclusive_lock(state_path: str | os.PathLike[str], *, timeout: float = 30.0, stale_after: float = 7200.0):
    """用 ``O_EXCL`` 锁住同一数据目录的状态提交阶段。

    这是不依赖 ``fcntl`` 的跨平台实现，macOS / Linux / Windows 都可用。
    锁文件内带唯一 token，释放时只删除自己创建的锁；超过两小时的残留锁
    可被后续进程回收。
    """
    lock_path = Path(state_path).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    token = f"{os.getpid()}:{uuid.uuid4().hex}"
    deadline = time.monotonic() + timeout
    acquired = False
    while not acquired:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > stale_after:
                    lock_path.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"等待状态锁超时：{lock_path}") from None
            time.sleep(0.1)
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(token)
            acquired = True
    try:
        yield
    finally:
        # 只有确认锁文件内容仍是自己的 token 才删除。读取失败（权限/占用/已被替换）
        # 时一律保留：宁可可被回收的残留锁，也不能误删其他进程正在持有的活锁。
        try:
            owns_lock = lock_path.read_text(encoding="utf-8") == token
        except OSError:
            owns_lock = False
        if owns_lock:
            with suppress(OSError):
                lock_path.unlink()


def prune_seen(seen: SeenMap, keep_days: int = config.SEEN_KEEP_DAYS, now: datetime | None = None) -> int:
    """丢弃过老记录，返回删除条数。

    日期直接读文章 URL 里的日期目录，按情况分两档：

    - **URL 带日**（``/2026-09/22/``、``/202609/22/``，六家实际都是这种）：按**整日**比较，
      保留窗口正好是最近的 ``keep_days`` 天；
    - **URL 只有年月**（少见）：退化为**整月**比较，只有当整个月都早于 cutoff 才丢，
      宁可多留也不误删。

    此前只按 ``strftime("%Y-%m")`` 做整月比较，实际窗口是
    ``keep_days + (cutoff.day - 1)`` 天 —— 也就是 120 天到 150 天之间浮动（取决于
    ``now - keep_days`` 落在月内哪一天），比这里的承诺最多多留一个月。
    两种比较都是定长 ``YYYY-MM-DD`` / ``YYYY-MM`` 字符串比较，等价于日期比较。
    """
    now = now or datetime.now()
    cutoff = now - timedelta(days=keep_days)
    cutoff_day = cutoff.strftime("%Y-%m-%d")
    cutoff_month = cutoff.strftime("%Y-%m")
    dropped = 0
    for bucket in seen.values():
        for url in list(bucket):
            match = _URL_DATE_RE.search(url)
            if not match:
                continue
            year, month, day = match.group(1), match.group(2), match.group(3)
            # URL 带日就按日比；只有年月时退化为整月比（保守方向：整月保留，宁可多留）
            url_key = f"{year}-{month}-{day}" if day else f"{year}-{month}"
            limit = cutoff_day if day else cutoff_month
            if url_key < limit:
                del bucket[url]
                dropped += 1
    return dropped


def record_hits(seen: SeenMap, hits: list[dict[str, Any]]) -> None:
    """把本次命中写入状态（同一 URL 对每个命中关键词各记一条）。"""
    for hit in hits:
        for keyword in hit.get("matched") or []:
            seen.setdefault(keyword, {})[hit["url"]] = 1


def compute_new_hits(
    all_hits: list[dict[str, Any]], seen: SeenMap, *, full: bool = False, no_state: bool = False
) -> list[dict[str, Any]]:
    """挑出本次的「新增命中」。

    - ``full`` / ``no_state``：本次全部命中都算新增（增量判定被显式关闭）。
    - 否则：命中所属的任一关键词桶里都没有该 URL，才算新增。
    """
    if full or no_state:
        return list(all_hits)

    new_hits: list[dict[str, Any]] = []
    for hit in all_hits:
        known_buckets = [seen[k] for k in (hit.get("matched") or []) if k in seen]
        if known_buckets:
            if all(hit["url"] not in bucket for bucket in known_buckets):
                new_hits.append(hit)
        elif not any(hit["url"] in bucket for bucket in seen.values()):
            new_hits.append(hit)
    return new_hits


# ---------------------------------------------------------------- 命中归档
# 归档文件格式版本：
#   v1 = 顶层是记录数组，layout 逐条挂在记录上（旧格式，仍可读）
#   v2 = {"version": 2, "layouts": {版面键: 布局}, "records": [...]}
# 同一版面的 layout 会被写到该版**每一条**命中上；逐条存等于把同一份坐标重复 N 次
# （一个 40 篇文章的版面 ≈ 重复 40 次）。v2 把它提到顶层按版面只存一份。
ARCHIVE_VERSION = 2
LAYOUT_KEY_SEP = "\u0001"


def layout_key(record: dict[str, Any]) -> str:
    """版面分组的键：``paper␁date␁section_no␁section``。

    取值口径必须与前端 ``render.js`` 的 ``layoutKeyOf`` 一致（前端据此从顶层
    ``layouts`` 表取回本版的布局）。
    """
    return LAYOUT_KEY_SEP.join(
        [
            str(record.get("paper") or ""),
            str(record.get("date") or ""),
            str(record.get("section_no") or ""),
            str(record.get("section") or "") or "—",
        ]
    )


def _pick_layout(layouts: dict[str, Any], key: str, layout: Any) -> None:
    """并入一份版面布局；同一版面出现多份时保留项数最多的那份。"""
    if not isinstance(layout, dict) or not isinstance(layout.get("items"), list) or not layout["items"]:
        return
    current = layouts.get(key)
    if current is None or len(layout["items"]) > len(current.get("items") or []):
        layouts[key] = layout


def split_archive(raw: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """把归档文件内容收敛成 ``(records, layouts)``，兼容 v1 与 v2。

    v1 记录上挂着的 ``layout`` 会被抽到 ``layouts`` 里按版面去重，记录本身不再携带，
    因此读侧只需处理「记录 + 顶层布局表」一种形状。
    """
    if isinstance(raw, dict):
        raw_records = raw.get("records")
        raw_layouts = raw.get("layouts")
    elif isinstance(raw, list):
        raw_records, raw_layouts = raw, None
    else:
        return [], {}

    records: list[dict[str, Any]] = []
    legacy_layouts: list[tuple[dict[str, Any], Any]] = []
    for item in raw_records if isinstance(raw_records, list) else []:
        normalized = normalize_archive_item(item)
        if normalized is None:
            continue
        legacy = normalized.pop("layout", None)
        if legacy is not None:
            legacy_layouts.append((normalized, legacy))
        records.append(normalized)

    layouts: dict[str, Any] = {}
    if isinstance(raw_layouts, dict):
        for key, layout in raw_layouts.items():
            if isinstance(key, str):
                _pick_layout(layouts, key, layout)
    for record, layout in legacy_layouts:
        _pick_layout(layouts, layout_key(record), layout)
    return records, layouts


def drop_orphan_layouts(records: list[dict[str, Any]], layouts: dict[str, Any]) -> dict[str, Any]:
    """丢掉不再被任何记录引用的版面布局（例如被 ``keep_days`` 剪掉的那些日期）。"""
    if not layouts:
        return {}
    alive = {layout_key(record) for record in records}
    return {key: layout for key, layout in layouts.items() if key in alive}


def read_archive(data_dir: os.PathLike[str] | str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """读取归档，返回 ``(records, layouts)``；文件缺失或损坏时返回空。"""
    try:
        with open(paths.archive_file(data_dir), encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return [], {}
    return split_archive(raw)


def save_archive(
    data_dir: os.PathLike[str] | str,
    records: list[dict[str, Any]],
    layouts: dict[str, Any],
) -> None:
    """原子写入 v2 归档。"""
    write_text(
        paths.archive_file(data_dir),
        json.dumps(
            {"version": ARCHIVE_VERSION, "layouts": layouts, "records": records},
            ensure_ascii=False,
            indent=1,
        ),
    )


def update_archive(
    data_dir: os.PathLike[str] | str,
    all_hits: list[dict[str, Any]],
    *,
    keep_days: int = 0,
    now: datetime | None = None,
    log: Callable[..., None] | None = None,
) -> list[dict[str, Any]]:
    """把本次命中并入 ``archive.json``，返回合并（并按需修剪）后的记录列表。

    旧归档中的局部脏数据会被跳过；同 URL 再次命中时更新标题、版面与
    几何信息，并对 ``matched`` 取有序并集，避免新关键词永远不进看板。

    写入的是 v2 归档：版面布局按 :func:`layout_key` 去重后放在顶层 ``layouts``，
    记录只保留自身很小的 ``geometry``；读取时仍兼容旧版数组格式。

    ``keep_days > 0`` 时顺带丢弃 ``date`` 早于该天数的条目（见 :func:`prune_archive`），
    默认 ``0`` 表示归档只增不删，保留完整审计历史。
    """
    records, layouts = read_archive(data_dir)
    by_url = {item["url"]: index for index, item in enumerate(records)}
    for hit in all_hits:
        normalized_hit = normalize_archive_item(hit)
        if normalized_hit is None:
            continue
        # 本次命中的版面布局先并入顶层表；记录本身不再逐条携带
        _pick_layout(layouts, layout_key(normalized_hit), normalized_hit.pop("layout", None))
        url = normalized_hit["url"]
        if url not in by_url:
            by_url[url] = len(records)
            records.append(normalized_hit)
            continue
        index = by_url[url]
        previous = records[index]
        merged = {**previous, **normalized_hit}
        merged["matched"] = list(
            dict.fromkeys([*(previous.get("matched") or []), *(normalized_hit.get("matched") or [])])
        )
        records[index] = merged

    records, dropped = prune_archive(records, keep_days, now)
    layouts = drop_orphan_layouts(records, layouts)
    save_archive(data_dir, records, layouts)
    if dropped and log:
        log(f"  （已清理 {dropped} 条早于最近 {keep_days} 天的归档记录）")
    return records


def prune_archive(
    archive: list[dict[str, Any]], keep_days: int, now: datetime | None = None
) -> tuple[list[dict[str, Any]], int]:
    """丢弃 ``date`` 早于最近 ``keep_days`` 天的归档条目，返回 ``(保留, 删除数)``。

    ``keep_days <= 0`` 时原样返回（默认保留完整历史）。``date`` 缺失或非法的条目一律保留，
    避免脏数据被误删。仅影响看板/报告用的 ``archive.json``，与 ``seen.json`` 的增量判定无关。
    """
    if keep_days <= 0:
        return archive, 0
    now = now or datetime.now()
    cutoff = (now - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    kept: list[dict[str, Any]] = []
    dropped = 0
    for item in archive:
        date = item.get("date") if isinstance(item, dict) else None
        if isinstance(date, str) and date and date < cutoff:
            dropped += 1
            continue
        kept.append(item)
    return kept, dropped


def normalize_archive_item(item: Any) -> dict[str, Any] | None:
    """把归档条目收敛为报告渲染必需的安全形状。"""
    if not isinstance(item, dict):
        return None
    url = item.get("url")
    if not isinstance(url, str) or not url:
        return None
    normalized = dict(item)
    normalized.update(
        {
            "paper": str(item.get("paper") or ""),
            "date": str(item.get("date") or ""),
            "section": str(item.get("section") or ""),
            "section_no": str(item.get("section_no") or ""),
            "title": str(item.get("title") or ""),
            "url": url,
            "snippets": [str(value) for value in item.get("snippets", []) if value]
            if isinstance(item.get("snippets"), list)
            else [],
            "matched": [str(value) for value in item.get("matched", []) if value]
            if isinstance(item.get("matched"), list)
            else [],
        }
    )
    try:
        normalized["text_len"] = int(item.get("text_len") or 0)
    except (TypeError, ValueError):
        normalized["text_len"] = 0
    return normalized
