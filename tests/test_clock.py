"""应用时钟（``clock``）的行为测试。

重点：
- 固定偏移与 IANA 名称都能解析；
- ``now()`` 返回 naive 墙上时间，可直接与旧代码的 ``datetime.now()`` 互换；
- 解析失败/环境变量异常时**绝不抛异常**，一律回落到缺省北京时间。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from newspaper_auto import clock


@pytest.fixture(autouse=True)
def _clear_clock_cache(monkeypatch):
    """每个用例前后都清缓存，避免环境变量与解析结果跨用例串味。"""
    monkeypatch.delenv("NEWSPAPER_TZ", raising=False)
    monkeypatch.delenv("TZ", raising=False)
    clock.reset_cache()
    yield
    clock.reset_cache()


def _expected(spec_offset: timedelta) -> datetime:
    return (datetime.now(timezone.utc) + spec_offset).replace(tzinfo=None)


def test_default_is_beijing_time():
    assert clock.now() - _expected(timedelta(hours=8)) < timedelta(seconds=5)


@pytest.mark.parametrize(
    "value,offset",
    [
        ("+08:00", timedelta(hours=8)),
        ("-05:30", timedelta(hours=-5, minutes=-30)),
        ("UTC+8", timedelta(hours=8)),
        ("GMT-3", timedelta(hours=-3)),
        ("+00:00", timedelta(0)),
        ("+9", timedelta(hours=9)),
    ],
)
def test_newspaper_tz_accepts_fixed_offsets(monkeypatch, value, offset):
    monkeypatch.setenv("NEWSPAPER_TZ", value)
    clock.reset_cache()
    assert clock.now() - _expected(offset) < timedelta(seconds=5)


def test_tz_env_is_used_when_newspaper_tz_absent(monkeypatch):
    """兼容 scan.yml 这类已显式设 TZ 的环境。"""
    monkeypatch.setenv("TZ", "+02:00")
    assert clock.now() - _expected(timedelta(hours=2)) < timedelta(seconds=5)


def test_newspaper_tz_wins_over_tz(monkeypatch):
    monkeypatch.setenv("TZ", "+02:00")
    monkeypatch.setenv("NEWSPAPER_TZ", "-06:00")
    assert clock.now() - _expected(timedelta(hours=-6)) < timedelta(seconds=5)


def test_iana_zone_name_resolves(monkeypatch):
    """IANA 名称走 zoneinfo；该环境没有 tz 数据库时跳过，而不是假装通过。"""
    try:
        from zoneinfo import ZoneInfo

        utc_offset = datetime.now(ZoneInfo("Asia/Tokyo")).utcoffset()
    except Exception:  # pragma: no cover - 依赖运行环境的 tz 数据库
        pytest.skip("本环境没有可用的 tz 数据库")
    monkeypatch.setenv("NEWSPAPER_TZ", "Asia/Tokyo")
    clock.reset_cache()
    assert clock.now() - _expected(utc_offset) < timedelta(seconds=5)


@pytest.mark.parametrize("bad", ["", "   ", "not a zone", "+99:00", "+08:99", "Mars/Olympus"])
def test_unparsable_spec_falls_back_to_default_instead_of_raising(monkeypatch, bad):
    monkeypatch.setenv("NEWSPAPER_TZ", bad)
    clock.reset_cache()
    assert clock.now() - _expected(timedelta(hours=8)) < timedelta(seconds=5)


def test_today_and_iso_shape():
    today = clock.today()
    assert clock.today_iso() == today.strftime("%Y-%m-%d")
    assert clock.now().date() == today
