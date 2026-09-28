"""扫描结果导出的路径与文件格式测试。"""

from __future__ import annotations

import csv
import errno
import io
import json
import re
from datetime import datetime

import pytest

from newspaper_auto import exporter

HITS = [
    {
        "paper": "湛江日报",
        "date": "2026-09-22",
        "section": "要闻",
        "title": "=危险公式",
        "url": "http://example.test/content_1.html",
        "text_len": 12,
        "snippets": ["…示例大学…"],
        "matched": ["示例大学"],
    }
]


def test_resolve_directory_resolves_relative_path_and_creates_parents(tmp_path):
    directory = exporter.resolve_directory("chosen/nested", base_dir=tmp_path, create=True)

    assert directory == (tmp_path / "chosen" / "nested").resolve()
    assert directory.is_dir()


@pytest.mark.parametrize("value", ["", "   ", "bad\npath", "bad\x7fpath"])
def test_resolve_directory_rejects_unsafe_values(value):
    with pytest.raises(exporter.ExportValidationError):
        exporter.resolve_directory(value)


def test_resolve_directory_rejects_existing_file(tmp_path):
    target = tmp_path / "not-a-directory"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(exporter.ExportValidationError, match="文件夹"):
        exporter.resolve_directory(target)


def test_export_csv_uses_safe_unique_filename_bom_and_formula_protection(tmp_path):
    now = datetime(2026, 9, 24, 10, 11, 12)
    first = exporter.export_hits(tmp_path, "csv", HITS, now=now)
    second = exporter.export_hits(tmp_path, "csv", HITS, now=now)

    assert first != second
    assert re.fullmatch(r"newspaper-hits-20260924-101112-[0-9a-f]{8}\.csv", first.name)
    assert first.parent == tmp_path.resolve()
    content = first.read_text(encoding="utf-8")
    assert content.startswith("\ufeff")
    row = next(csv.DictReader(io.StringIO(content.lstrip("\ufeff"))))
    assert row["title"] == "'=危险公式"
    assert not list(tmp_path.glob(".*.tmp"))


def test_export_json_roundtrip_and_trailing_newline(tmp_path):
    target = exporter.export_hits(tmp_path / "new", "JSON", HITS)

    content = target.read_text(encoding="utf-8")
    assert target.suffix == ".json"
    assert content.endswith("\n")
    assert json.loads(content) == HITS


def test_invalid_format_does_not_create_directory(tmp_path):
    target_dir = tmp_path / "must-not-exist"

    with pytest.raises(exporter.ExportValidationError, match="csv 或 json"):
        exporter.export_hits(target_dir, "xml", HITS)

    assert not target_dir.exists()


@pytest.mark.parametrize("value", [None, "", "xml", 1])
def test_normalize_format_rejects_unsupported_values(value):
    with pytest.raises(exporter.ExportValidationError, match="csv 或 json"):
        exporter.normalize_format(value)


def test_write_failure_reports_folder_in_plain_language(tmp_path, monkeypatch):
    """写盘失败要给出「往哪儿写、为什么写不进去」，而不是裸 OSError 原文。"""

    def fail_write(*_args, **_kwargs):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(exporter, "write_text", fail_write)

    with pytest.raises(exporter.ExportWriteError) as exc_info:
        exporter.export_hits(tmp_path, "csv", HITS)

    error = exc_info.value
    assert isinstance(error, OSError), "HTTP 层仍按 OSError 归类为 500"
    message = str(error)
    assert str(tmp_path) in message
    assert "EPERM" in message
    assert "[Errno" not in message, "不要把 OS 原文直接抛给使用者"


def test_create_directory_failure_also_reports_in_plain_language(tmp_path, monkeypatch):
    """建目录就失败时同样翻成人话（此时还拿不到规范化路径，用调用方给的原值）。"""

    def fail_resolve(*_args, **_kwargs):
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(exporter, "resolve_directory", fail_resolve)

    with pytest.raises(exporter.ExportWriteError) as exc_info:
        exporter.export_hits(tmp_path, "csv", HITS)

    message = str(exc_info.value)
    assert str(tmp_path) in message
    assert "EACCES" in message


def test_validation_errors_are_not_reported_as_write_errors(tmp_path):
    """参数不合法仍走 400，不能被包装成「写不进去」。"""

    with pytest.raises(exporter.ExportValidationError):
        exporter.export_hits(tmp_path, "xml", HITS)
