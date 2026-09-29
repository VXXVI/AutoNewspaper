"""增量状态读写与「新增」判定的单元测试。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from newspaper_auto import state


def test_load_missing_file_returns_empty(tmp_path):
    assert state.load_seen(tmp_path / "nope.json") == {}


def test_load_corrupt_file_returns_empty(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text("{ not json", encoding="utf-8")
    assert state.load_seen(path) == {}


def test_load_non_dict_returns_empty(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert state.load_seen(path) == {}


def test_load_seen_normalizes_nested_dirty_data(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text(
        '{"good":{"u1":99,"":1},"bad":7,"":{"u2":1}}',
        encoding="utf-8",
    )
    assert state.load_seen(path) == {"good": {"u1": 1}}


def test_save_load_roundtrip_and_no_tmp_leftover(tmp_path):
    path = tmp_path / "state" / "seen.json"
    data = {"示例大学": {"http://x/2026-09/22/content_1.html": 1}}
    state.save_seen(path, data)
    assert state.load_seen(path) == data
    assert list(path.parent.glob("*.tmp")) == []


def test_exclusive_lock_is_released(tmp_path):
    path = tmp_path / "state" / "seen.json"
    lock_path = path.with_suffix(".lock")
    with state.exclusive_lock(path):
        assert lock_path.exists()
    assert not lock_path.exists()


def test_exclusive_lock_keeps_foreign_lock_on_read_error(tmp_path, monkeypatch):
    """读取锁内容失败时不得 unlink：那可能是其他进程正在持有的活锁。"""
    path = tmp_path / "state" / "seen.json"
    lock_path = path.with_suffix(".lock")
    original = Path.read_text

    def flaky_read_text(self, *args, **kwargs):
        if self == lock_path:
            raise PermissionError("模拟读取失败")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    with state.exclusive_lock(path):
        assert lock_path.exists()

    assert lock_path.exists(), "读取失败时不应删除锁文件"


def test_prune_seen_drops_old_but_keeps_recent_and_dateless():
    now = datetime(2026, 9, 22)
    seen = {
        "k": {
            "http://x/2026-09/22/content_1.html": 1,
            "http://x/2026-01/01/content_2.html": 1,
            "http://x/nodate/content_3.html": 1,
        }
    }
    dropped = state.prune_seen(seen, keep_days=120, now=now)
    assert dropped == 1
    assert set(seen["k"]) == {
        "http://x/2026-09/22/content_1.html",
        "http://x/nodate/content_3.html",
    }


def test_prune_seen_window_is_exactly_keep_days_for_urls_with_a_day():
    """回归：整月比较让实际窗口在 keep_days ~ keep_days+30 天之间浮动。

    现在按 URL 里的**日**比较，窗口正好是 keep_days 天：第 keep 天前的那篇留，再往前一天丢。
    """
    now = datetime(2026, 9, 29)
    keep = 120
    inside = now - timedelta(days=keep - 1)  # 窗口内最后一天
    boundary = now - timedelta(days=keep)  # 正好 keep 天前
    outside = now - timedelta(days=keep + 1)  # 窗口外

    def url(when: datetime, stem: str) -> str:
        return f"http://x/{when:%Y-%m}/{when:%d}/{stem}.html"

    seen = {"k": {url(inside, "a"): 1, url(boundary, "b"): 1, url(outside, "c"): 1}}

    assert state.prune_seen(seen, keep_days=keep, now=now) == 1
    assert set(seen["k"]) == {url(inside, "a"), url(boundary, "b")}


def test_prune_seen_window_does_not_depend_on_where_cutoff_lands_in_the_month():
    """无论 cutoff 落在月内哪一天，窗口都必须恰好是 keep_days 天，且绝不误删窗口内记录。"""
    keep = 120
    for day in range(1, 29):
        now = datetime(2026, 8, day)
        edge = now - timedelta(days=keep)
        old = now - timedelta(days=keep + 1)
        edge_url = f"http://x/{edge:%Y-%m}/{edge:%d}/a.html"
        seen = {"k": {edge_url: 1, f"http://x/{old:%Y-%m}/{old:%d}/b.html": 1}}

        state.prune_seen(seen, keep_days=keep, now=now)

        assert set(seen["k"]) == {edge_url}, f"now={now:%F} 误删了仍在 {keep} 天窗口内的记录"


def test_prune_seen_falls_back_to_month_granularity_when_url_has_no_day():
    """URL 只有年月时保守处理：只有整个月都早于 cutoff 才丢，宁可多留。"""
    # now - 120 天 = 2026-06-01 → cutoff 所在月 = 2026-06
    seen = {
        "k": {
            "http://x/2026-05/content_a.html": 1,  # 整月都在窗口外 → 丢
            "http://x/2026-06/content_b.html": 1,  # 与 cutoff 同月 → 保守保留
            "http://x/nodate/content_c.html": 1,  # 读不出日期 → 保留
        }
    }
    assert state.prune_seen(seen, keep_days=120, now=datetime(2026, 9, 29)) == 1
    assert set(seen["k"]) == {"http://x/2026-06/content_b.html", "http://x/nodate/content_c.html"}


def test_prune_seen_reads_compact_date_directories():
    """/202609/29/ 这种紧凑日期目录也要能读出日期（正则的 ``-?`` 分支）。"""
    seen = {"k": {"http://x/202609/29/a.html": 1, "http://x/202601/01/b.html": 1}}
    assert state.prune_seen(seen, keep_days=120, now=datetime(2026, 9, 29)) == 1
    assert set(seen["k"]) == {"http://x/202609/29/a.html"}


def test_record_hits_marks_each_matched_keyword():
    seen: dict = {}
    hits = [{"url": "u1", "matched": ["甲", "乙"]}, {"url": "u2", "matched": ["甲"]}]
    state.record_hits(seen, hits)
    assert seen == {"甲": {"u1": 1, "u2": 1}, "乙": {"u1": 1}}


# ---------------------------------------------------------------- 新增快照
def test_save_and_load_last_new_roundtrip_dedupes_and_skips_dirty(tmp_path):
    path = tmp_path / "last_new.json"
    hits = [
        {"url": "u1"},
        {"url": "u1"},  # 重复 URL 只留一次
        {"url": ""},  # 空 URL 丢掉
        {"matched": ["x"]},  # 没有 url 的脏数据丢掉
        "not-a-dict",
    ]
    state.save_last_new(path, hits, generated_at=datetime(2026, 9, 22, 10, 0, 0))
    loaded = state.load_last_new(path)
    assert loaded == {"generated_at": "2026-09-22 10:00:00", "urls": ["u1"]}


def test_load_last_new_tolerates_missing_and_corrupt(tmp_path):
    assert state.load_last_new(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    assert state.load_last_new(bad) is None
    bad.write_text('{"urls": "应失配"}', encoding="utf-8")
    assert state.load_last_new(bad) is None


# ---------------------------------------------------------------- 归档 v2
def _layout(items: int) -> dict:
    return {
        "basis": "area",
        "aspect_ratio": 0.72,
        "items": [
            {"url": f"http://x/{i}", "left": 0.1, "top": i / items, "right": 0.4, "bottom": (i + 1) / items}
            for i in range(items)
        ],
    }


def test_layout_key_matches_frontend_contract():
    """分组键口径必须与前端 render.js 的 layoutKeyOf 一致。"""
    record = {"paper": "甲报", "date": "2026-09-23", "section_no": "第02版", "section": "第2版：要闻"}
    assert state.layout_key(record) == "甲报\u00012026-09-23\u0001第02版\u0001第2版：要闻"
    # 缺失字段：版面名回落 "—"，section_no 回落空串
    assert state.layout_key({"paper": "A", "date": "2026-01-01"}) == "A\u00012026-01-01\u0001\u0001—"


def test_update_archive_writes_v2_and_dedupes_shared_layout(tmp_path):
    """同一版面多条命中在**磁盘上**只存一份 layout（回归：曾逐条重复，占体积 90%）。"""
    layout = _layout(5)
    hits = [
        {
            "url": f"http://x/{i}",
            "paper": "甲报",
            "date": "2026-09-23",
            "section": "第2版：要闻",
            "section_no": "第02版",
            "layout": layout,
            "matched": ["k"],
        }
        for i in range(5)
    ]
    state.update_archive(tmp_path, hits)

    on_disk = json.loads((tmp_path / "hits" / "archive.json").read_text(encoding="utf-8"))
    assert on_disk["version"] == state.ARCHIVE_VERSION
    assert len(on_disk["records"]) == 5
    assert all("layout" not in rec for rec in on_disk["records"]), "记录不再逐条携带 layout"
    assert len(on_disk["layouts"]) == 1, "同一版面只存一份"
    assert len(next(iter(on_disk["layouts"].values()))["items"]) == 5

    # 读回来仍是 (records, layouts)，形状与写入一致
    records, layouts = state.read_archive(tmp_path)
    assert len(records) == 5 and len(layouts) == 1
    assert all("layout" not in rec for rec in records)


def test_split_archive_accepts_legacy_v1_records(tmp_path):
    """v1（顶层数组、layout 逐条挂）仍可读：布局被抽到顶层并按版面去重。"""
    layout = _layout(3)
    legacy = [
        {"url": "u1", "paper": "甲报", "date": "2026-09-23", "section_no": "第02版", "layout": layout},
        {"url": "u2", "paper": "甲报", "date": "2026-09-23", "section_no": "第02版", "layout": layout},
    ]
    records, layouts = state.split_archive(legacy)
    assert [rec["url"] for rec in records] == ["u1", "u2"]
    assert all("layout" not in rec for rec in records)
    assert len(layouts) == 1
    assert len(next(iter(layouts.values()))["items"]) == 3


def test_split_archive_rejects_garbage():
    assert state.split_archive("不是列表也不是对象") == ([], {})
    assert state.split_archive({"records": "坏了", "layouts": []}) == ([], {})
    # 缺 url 的记录被丢弃，损坏的 layout 被忽略
    records, layouts = state.split_archive({"records": [{"title": "无 url"}], "layouts": {"k": "坏了"}})
    assert records == [] and layouts == {}


def test_drop_orphan_layouts_removes_unreferenced_sections():
    records = [{"url": "u1", "paper": "甲报", "date": "2026-09-23", "section": "要闻"}]
    key = state.layout_key(records[0])
    layouts = {key: _layout(2), "别的\u0001版面\u0001\u0001—": _layout(2)}
    assert set(state.drop_orphan_layouts(records, layouts)) == {key}
    assert state.drop_orphan_layouts(records, {}) == {}


def test_archive_layout_is_refreshed_when_a_scan_brings_a_better_one(tmp_path):
    """同一版面后续扫到项数更多的布局时，顶层表应更新为更完整的那份。"""
    section = {"paper": "甲报", "date": "2026-09-23", "section": "第2版：要闻", "section_no": "第02版"}
    state.update_archive(tmp_path, [{**section, "url": "u1", "layout": _layout(2), "matched": ["k"]}])
    state.update_archive(tmp_path, [{**section, "url": "u2", "layout": _layout(6), "matched": ["k"]}])

    _records, layouts = state.read_archive(tmp_path)
    assert len(layouts) == 1
    assert len(next(iter(layouts.values()))["items"]) == 6


def test_compute_new_hits_presence_based():
    hits = [{"url": "u1", "matched": ["a"]}, {"url": "u2", "matched": ["a"]}]
    seen = {"a": {"u1": 1}}
    assert state.compute_new_hits(hits, seen) == [hits[1]]


def test_compute_new_hits_full_and_no_state_report_all():
    hits = [{"url": "u1", "matched": ["a"]}]
    seen = {"a": {"u1": 1}}
    assert state.compute_new_hits(hits, seen, full=True) == hits
    assert state.compute_new_hits(hits, seen, no_state=True) == hits


def test_compute_new_hits_unknown_keyword_uses_global_fallback():
    hits = [{"url": "u1", "matched": ["新词"]}]
    assert state.compute_new_hits(hits, {"a": {"u1": 1}}) == []
    assert state.compute_new_hits(hits, {"a": {"u9": 1}}) == hits


def test_compute_new_hits_without_matched_field():
    hits = [{"url": "u1"}]
    assert state.compute_new_hits(hits, {"a": {"u1": 1}}) == []
    assert state.compute_new_hits(hits, {}) == hits
