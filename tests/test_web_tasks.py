"""看板后台任务状态模型测试。"""

from __future__ import annotations

import threading

from newspaper_auto.web_tasks import ScanTask


def test_snapshot_is_detached_and_nonzero_exit_is_error():
    task = ScanTask.running(
        {"keywords": ["海大"], "days": 1},
        total_units=2,
        max_log_lines=10,
    )
    task.append_log("  ✓ 南方日报 09-24 版面 1")
    task.finish({"exit_code": 1, "summary": {"hits": 2, "details": ["partial"]}})

    snapshot = task.snapshot()
    assert snapshot["status"] == "error"
    assert snapshot["exit_code"] == 1
    assert "退出码 1" in snapshot["error"]
    assert snapshot["progress"]["completed"] == 1

    snapshot["params"]["keywords"].append("污染")
    snapshot["summary"]["details"].append("污染")
    clean = task.snapshot()
    assert clean["params"]["keywords"] == ["海大"]
    assert clean["summary"]["details"] == ["partial"]


def test_incremental_cursor_and_truncation():
    task = ScanTask.running({"keywords": ["x"]}, total_units=0, max_log_lines=3)
    for line in ("one", "two", "three", "four"):
        task.append_log(line)

    latest = task.snapshot(after=2)
    assert latest["log"] == ["three", "four"]
    assert latest["log_cursor"] == 4
    assert latest["logs_truncated"] is False

    expired = task.snapshot(after=0)
    assert expired["log"] == ["two", "three", "four"]
    assert expired["logs_truncated"] is True
    assert task.snapshot(after=4)["log"] == []

    future = task.snapshot(after=99)
    assert future["logs_truncated"] is True
    assert future["log"] == ["two", "three", "four"]


def test_concurrent_log_writes_and_snapshots_are_consistent():
    task = ScanTask.running({"keywords": ["x"]}, total_units=100, max_log_lines=12)

    def write_logs() -> None:
        for index in range(100):
            task.append_log(f"  ✓ 报社 {index}")

    writer = threading.Thread(target=write_logs)
    writer.start()
    previous_cursor = 0
    while writer.is_alive():
        snapshot = task.snapshot()
        assert snapshot["log_cursor"] >= previous_cursor
        assert len(snapshot["log"]) <= 12
        assert snapshot["progress"]["completed"] <= 100
        previous_cursor = snapshot["log_cursor"]
    writer.join()

    final = task.snapshot()
    assert final["log_cursor"] == 100
    assert final["progress"] == {
        "completed": 100,
        "total": 100,
        "percent": 100,
        "current": "✓ 报社 99",
    }
