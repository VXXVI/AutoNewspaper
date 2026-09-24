"""增量状态读写与「新增」判定的单元测试。"""

from __future__ import annotations

from datetime import datetime

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
    data = {"广东海洋大学": {"http://x/2026-09/22/content_1.html": 1}}
    state.save_seen(path, data)
    assert state.load_seen(path) == data
    assert list(path.parent.glob("*.tmp")) == []


def test_exclusive_lock_is_released(tmp_path):
    path = tmp_path / "state" / "seen.json"
    lock_path = path.with_suffix(".lock")
    with state.exclusive_lock(path):
        assert lock_path.exists()
    assert not lock_path.exists()


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


def test_record_hits_marks_each_matched_keyword():
    seen: dict = {}
    hits = [{"url": "u1", "matched": ["甲", "乙"]}, {"url": "u2", "matched": ["甲"]}]
    state.record_hits(seen, hits)
    assert seen == {"甲": {"u1": 1, "u2": 1}, "乙": {"u1": 1}}


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
