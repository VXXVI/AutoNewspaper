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
from typing import Any

from . import config, report

# 文章 URL 内含日期目录，如 /2026-09/22/ 或 /202609/22/
_URL_DATE_RE = re.compile(r"/(\d{4})-?(\d{2})/")

SeenMap = dict[str, dict[str, int]]


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
    report.write_text(path, json.dumps(seen, ensure_ascii=False, indent=1))


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
        try:
            if lock_path.read_text(encoding="utf-8") == token:
                lock_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            with suppress(OSError):
                lock_path.unlink()


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
