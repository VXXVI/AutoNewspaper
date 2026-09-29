"""应用时钟：把「今天」钉在项目约定的时区上。

本项目监测的是中国报纸，出刊日按北京时间。但三处「今天」的口径可能不一致：

- GitHub runner 默认 UTC（``scan.yml`` 因此显式设了 ``TZ=Asia/Shanghai``）；
- VPS 上的后台看板通常也是 UTC；
- 而浏览器用的是**用户本地**时区。

若各自用「本机 ``datetime.now()``」判断今天，就会出现「前端允许选、后端判为未来」
这类跨时区错位（连同 ``MAX_RANGE_DAYS`` 默认区间一起偏一天）。这里统一提供一份
墙上时间，让服务端与浏览器对齐。

解析顺序（首个非空者生效，按值缓存）：

1. ``NEWSPAPER_TZ`` —— IANA 名称（``Asia/Shanghai``）或固定偏移（``+08:00`` / ``-05:30`` / ``UTC+8``）；
2. ``TZ`` —— 兼容已显式设过 TZ 的环境（``scan.yml``、部分 shell / 容器）；
3. 缺省 ``+08:00``（北京时间，中国报纸出刊口径）。

IANA 名称走 ``zoneinfo``（Python 3.9+，按调用时刻取偏移，自动处理夏令时）；系统没有
tz 数据库时（Windows 未装 ``tzdata``、或名称拼错）退回固定偏移解析，再退回缺省。
**任何情况下都不抛异常**——时钟坏掉不该让扫描挂掉。
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone

# 缺省时区：中国报纸按北京时间出刊。
DEFAULT_TZ = "+08:00"

# ``+08:00`` / ``-05:30`` / ``UTC+8`` / ``GMT-3`` —— 只认带符号的固定偏移。
_OFFSET_RE = re.compile(r"^(?:UTC|GMT)?\s*([+-])(\d{1,2})(?::?(\d{2}))?$", re.IGNORECASE)

# 以「时区规格字符串」为键的解析结果缓存：("zone", ZoneInfo) 或 ("offset", timedelta)。
# 只缓存解析结果（ZoneInfo 本身按需取偏移），不做 DST 偏移的长期缓存。
_cache: dict[str, tuple[str, object]] = {}


def _parse_offset(text: str) -> timedelta | None:
    """把 ``+08:00`` / ``UTC+8`` 这类固定偏移解析成 ``timedelta``；解析不了返回 None。"""
    match = _OFFSET_RE.match(text.strip())
    if not match:
        return None
    hours = int(match.group(2))
    minutes = int(match.group(3) or 0)
    if hours > 23 or minutes > 59:
        return None
    delta = timedelta(hours=hours, minutes=minutes)
    return delta if match.group(1) == "+" else -delta


def _build(spec: str) -> tuple[str, object]:
    """把时区规格解析成 ``("offset", timedelta)`` 或 ``("zone", ZoneInfo)``。"""
    offset = _parse_offset(spec)
    if offset is not None:
        return ("offset", offset)
    try:  # IANA 名称：zoneinfo 缺失或名称未知时走下面的兜底
        from zoneinfo import ZoneInfo

        return ("zone", ZoneInfo(spec))
    except Exception:
        pass
    return ("offset", _parse_offset(DEFAULT_TZ) or timedelta(0))


def _source() -> tuple[str, object]:
    spec = (os.environ.get("NEWSPAPER_TZ") or os.environ.get("TZ") or DEFAULT_TZ).strip()
    if spec not in _cache:
        _cache[spec] = _build(spec)
    return _cache[spec]


def reset_cache() -> None:
    """清空解析缓存。测试改动环境变量后调用，避免跨用例串味。"""
    _cache.clear()


def now() -> datetime:
    """应用时区的**墙上时间**。

    返回 naive ``datetime``，形状与 ``datetime.now()`` 完全一致，因此可以直接替换
    现有比较与 ``strftime``，无需改动调用方。
    """
    kind, value = _source()
    if kind == "zone":
        return datetime.now(value).replace(tzinfo=None)  # type: ignore[arg-type]
    return (datetime.now(timezone.utc) + value).replace(tzinfo=None)  # type: ignore[operator]


def today() -> date:
    """应用时区下的「今天」。"""
    return now().date()


def today_iso() -> str:
    """应用时区下的「今天」，``YYYY-MM-DD``。"""
    return now().strftime("%Y-%m-%d")
