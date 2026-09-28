"""``fsutil`` 把 OS 级错误翻成人话的规则。"""

from __future__ import annotations

import errno
import os
from pathlib import Path

from newspaper_auto import fsutil


def test_known_errno_gets_actionable_reason_and_code():
    message = fsutil.explain_oserror(PermissionError(errno.EPERM, "Operation not permitted"))

    assert "系统拒绝了写入" in message
    assert "只读" in message
    assert message.endswith("（EPERM）"), "带上 errno 名便于搜索与报障"


def test_unknown_errno_falls_back_to_strerror():
    message = fsutil.explain_oserror(OSError(errno.EEXIST, "File exists"))

    assert "File exists" in message
    assert message.endswith("（EEXIST）")


def test_error_without_errno_falls_back_to_text():
    assert fsutil.explain_oserror(OSError("磁盘只读")) == "磁盘只读"


def test_write_text_reports_and_cleans_up_on_failure(tmp_path, monkeypatch):
    """失败时不留半截临时文件——这是 ``write_text`` 存在的理由。"""

    def fail_replace(*_args, **_kwargs):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "replace", fail_replace)

    target = tmp_path / "out.json"
    try:
        fsutil.write_text(target, "{}")
    except PermissionError:
        pass
    else:  # pragma: no cover - 防御：改名失败必须冒泡
        raise AssertionError("替换失败时应当抛出 OSError")

    assert not target.exists()
    assert list(tmp_path.iterdir()) == [], "临时文件必须被清掉"


def test_write_text_writes_utf8_and_creates_parents(tmp_path):
    target = Path(tmp_path) / "nested" / "out.txt"

    fsutil.write_text(target, "中文\n")

    assert target.read_text(encoding="utf-8") == "中文\n"
    assert not list(target.parent.glob(".*.tmp"))
