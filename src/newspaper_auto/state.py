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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import config

# 文章 URL 内含日期目录，如 /2026-09/22/ 或 /202609/22/
_URL_DATE_RE = re.compile(r"/(\d{4})-?(\d{2})/")

SeenMap = dict[str, dict[str, int]]


def load_seen(path: str | os.PathLike[str]) -> SeenMap:
    """读取增量状态；文件缺失或损坏时返回空字典（不抛异常）。"""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_seen(path: str | os.PathLike[str], seen: SeenMap) -> None:
    """原子写入增量状态（先写临时文件再替换，避免中断导致半截文件）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(seen, handle, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def prune_seen(seen: SeenMap, keep_days: int = config.SEEN_KEEP_DAYS, now: datetime | None = None) -> int:
    """丢弃过老记录，返回删除条数。

    文章 URL 里含日期目录，直接从中读日期，只保留最近 ``keep_days`` 天。
    """
    now = now or datetime.now()
    cutoff = (now - timedelta(days=keep_days)).strftime("%Y-%m")
    dropped = 0
    for bucket in seen.values():
        for url in list(bucket):
            match = _URL_DATE_RE.search(url)
            if match and f"{match.group(1)}-{match.group(2)}" < cutoff:
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
