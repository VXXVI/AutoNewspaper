"""自定义报社（``papers.py``）的离线单元测试：全部用 ``tmp_path``，不碰真实项目根。"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from newspaper_auto import config, papers


def _valid(**overrides):
    entry = {"name": "测试报", "style": "ymd", "tpl": "https://example.com/{a}/node_01.html"}
    entry.update(overrides)
    return entry


# ---------------------------------------------------------------- validate_paper
def test_validate_paper_minimal_ok():
    paper, error = papers.validate_paper(_valid())
    assert error is None
    assert paper == {"name": "测试报", "style": "ymd", "tpl": "https://example.com/{a}/node_01.html"}


def test_validate_paper_accepts_optional_fields():
    paper, error = papers.validate_paper(
        _valid(
            style="dash",
            timeout=30,
            retries=2,
            node_exclude="/pad/",
            node_pattern=r"page-\d+\.html$",
            article_pattern=r"story-\d+\.html$",
        )
    )
    assert error is None
    assert paper["style"] == "dash"
    assert paper["timeout"] == 30
    assert paper["retries"] == 2
    assert paper["node_exclude"] == "/pad/"
    assert paper["node_pattern"] == r"page-\d+\.html$"
    assert paper["article_pattern"] == r"story-\d+\.html$"


def test_validate_custom_date_format_and_format_path():
    paper, error = papers.validate_paper(_valid(style="custom", date_format="%Y/%m/%d"))
    assert error is None
    assert papers.format_date_path(paper, datetime(2026, 9, 24)) == "2026/09/24"


def test_validate_paper_strips_whitespace():
    paper, error = papers.validate_paper(_valid(name="  测试报  "))
    assert error is None
    assert paper["name"] == "测试报"


@pytest.mark.parametrize(
    "obj",
    [
        "not-a-dict",
        {},
        {"name": "", "style": "ymd", "tpl": "https://x/{a}"},
        {"name": "x" * 31, "style": "ymd", "tpl": "https://x/{a}"},
        {"name": "x", "style": "weekly", "tpl": "https://x/{a}"},
        {"name": "x", "style": "ymd", "tpl": "https://x/static"},  # 缺 {a}
        {"name": "x", "style": "ymd", "tpl": "ftp://x/{a}"},  # 非 http(s)
        {"name": "x", "style": "ymd", "tpl": "example.com/{a}"},  # 无协议
        {"name": "x", "style": "ymd", "tpl": "https://x/{a}/{other}"},  # 多余占位符
        {"name": "x", "style": "ymd", "tpl": "https://x/{a!r}"},  # 不允许 conversion
        {"name": "x", "style": "ymd", "tpl": "https://x/{a:>10}"},  # 不允许 format spec
        {"name": "x", "style": "ymd", "tpl": "https://x/{a"},  # 花括号未闭合
        {"name": "x", "style": "ymd", "tpl": "https://x/{a}", "timeout": 4},
        {"name": "x", "style": "ymd", "tpl": "https://x/{a}", "timeout": 121},
        {"name": "x", "style": "ymd", "tpl": "https://x/{a}", "timeout": True},
        {"name": "x", "style": "ymd", "tpl": "https://x/{a}", "retries": -1},
        {"name": "x", "style": "ymd", "tpl": "https://x/{a}", "retries": 7},
        {"name": "x", "style": "ymd", "tpl": "https://x/{a}", "node_exclude": "("},
        {"name": "x", "style": "ymd", "tpl": "https://x/{a}", "node_exclude": 3},
        {"name": "测试,报", "style": "ymd", "tpl": "https://x/{a}"},
        {"name": "测试\n报", "style": "ymd", "tpl": "https://x/{a}"},
        {"name": "x", "style": "ymd", "tpl": "https:///{a}"},
        {"name": "x", "style": "ymd", "tpl": "https://x:abc/{a}"},
        {"name": "x", "style": "custom", "tpl": "https://x/{a}"},
        {"name": "x", "style": "custom", "tpl": "https://x/{a}", "date_format": "%Y/%m"},
        {"name": "x", "style": "custom", "tpl": "https://x/{a}", "date_format": "%Y/%m/%d/%H"},
        {"name": "x", "style": "ymd", "tpl": "https://x/{a}", "date_format": "%Y/%m/%d"},
        {"name": "x", "style": "ymd", "tpl": "https://x/{a}", "node_pattern": "("},
        {"name": "x", "style": "ymd", "tpl": "https://x/{a}", "article_pattern": "("},
    ],
)
def test_validate_paper_rejects_invalid(obj):
    paper, error = papers.validate_paper(obj)
    assert paper is None
    assert error


# ---------------------------------------------------------------- merge_papers
def test_merge_papers_appends_new_after_builtin():
    builtin = [{"name": "甲", "style": "ymd", "tpl": "https://a/{a}"}]
    custom = [{"name": "乙", "style": "dash", "tpl": "https://b/{a}"}]
    merged = papers.merge_papers(builtin, custom)
    assert [p["name"] for p in merged] == ["甲", "乙"]


def test_merge_papers_same_name_overrides_in_place():
    builtin = [
        {"name": "甲", "style": "ymd", "tpl": "https://a/{a}"},
        {"name": "乙", "style": "ymd", "tpl": "https://b/{a}"},
    ]
    override = {"name": "甲", "style": "dash", "tpl": "https://new/{a}"}
    merged = papers.merge_papers(builtin, [override])
    assert [p["name"] for p in merged] == ["甲", "乙"]  # 顺序不变、不重复
    assert merged[0]["tpl"] == "https://new/{a}"
    assert merged[1]["tpl"] == "https://b/{a}"


def test_merge_papers_does_not_mutate_inputs():
    builtin = [{"name": "甲", "style": "ymd", "tpl": "https://a/{a}"}]
    custom = [{"name": "甲", "style": "dash", "tpl": "https://new/{a}"}]
    papers.merge_papers(builtin, custom)
    assert builtin[0]["tpl"] == "https://a/{a}"  # 原列表未被改动


def test_merge_papers_excludes_disabled_builtin_override_and_custom():
    builtin = [
        {"name": "甲", "style": "ymd", "tpl": "https://a/{a}"},
        {"name": "乙", "style": "ymd", "tpl": "https://b/{a}"},
    ]
    custom = [
        {"name": "甲", "style": "dash", "tpl": "https://new-a/{a}"},
        {"name": "丙", "style": "dash", "tpl": "https://c/{a}"},
    ]

    merged = papers.merge_papers(builtin, custom, disabled=["甲", "丙"])

    assert merged == [{"name": "乙", "style": "ymd", "tpl": "https://b/{a}"}]


# ---------------------------------------------------------------- load_custom
def test_load_custom_missing_file_returns_empty(tmp_path):
    assert papers.load_custom(tmp_path) == []


def test_load_settings_legacy_file_without_disabled_is_compatible(tmp_path):
    """旧版 version=1 文件没有 disabled，宽松/严格读取都视为未停用。"""
    entry = _valid(name="旧配置报")
    (tmp_path / config.CUSTOM_PAPERS_FILENAME).write_text(
        json.dumps({"version": 1, "papers": [entry]}, ensure_ascii=False),
        encoding="utf-8",
    )

    expected = papers.PaperSettings(custom=(entry,), disabled=())
    assert papers.load_settings(tmp_path) == expected
    assert papers.load_settings_for_update(tmp_path) == expected


def test_version_one_file_ignores_unknown_paper_fields_but_api_validation_is_strict(tmp_path):
    entry = _valid(name="旧扩展报")
    extended = {**entry, "note": "历史版本自定义备注"}
    (tmp_path / config.CUSTOM_PAPERS_FILENAME).write_text(
        json.dumps({"version": 1, "papers": [extended]}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert papers.load_settings(tmp_path).custom == (entry,)
    assert papers.load_settings_for_update(tmp_path).custom == (entry,)
    assert papers.validate_paper(extended)[1] == "不支持的字段：note"


def test_load_custom_corrupt_json_returns_empty(tmp_path, capsys):
    (tmp_path / config.CUSTOM_PAPERS_FILENAME).write_text("{ oops", encoding="utf-8")
    assert papers.load_custom(tmp_path) == []
    assert "警告" in capsys.readouterr().err


def test_load_custom_wrong_top_level_returns_empty(tmp_path, capsys):
    (tmp_path / config.CUSTOM_PAPERS_FILENAME).write_text("[1, 2, 3]", encoding="utf-8")
    assert papers.load_custom(tmp_path) == []
    assert "警告" in capsys.readouterr().err


def test_load_custom_skips_invalid_entries(tmp_path, capsys):
    payload = {
        "version": 1,
        "papers": [
            _valid(name="好报社"),
            {"name": "坏报社", "style": "ymd", "tpl": "no-{a}-scheme"},  # 非法：无协议
            _valid(name="也好"),
        ],
    }
    (tmp_path / config.CUSTOM_PAPERS_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    loaded = papers.load_custom(tmp_path)
    assert [p["name"] for p in loaded] == ["好报社", "也好"]
    assert "已跳过" in capsys.readouterr().err


def test_load_custom_skips_duplicate_names_and_unsupported_version(tmp_path, capsys):
    path = tmp_path / config.CUSTOM_PAPERS_FILENAME
    path.write_text(
        json.dumps({"version": 1, "papers": [_valid(name="甲"), _valid(name="甲")]}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert papers.load_custom(tmp_path) == [_valid(name="甲")]
    assert "同名" in capsys.readouterr().err

    path.write_text(json.dumps({"version": 2, "papers": [_valid(name="乙")]}), encoding="utf-8")
    assert papers.load_custom(tmp_path) == []
    assert "不支持" in capsys.readouterr().err


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"version": 2, "papers": []}, "version"),
        ({"version": 1, "papers": [_valid(name="甲"), _valid(name="甲")]}, "重复"),
    ],
)
def test_load_custom_for_update_rejects_ambiguous_files(tmp_path, payload, message):
    (tmp_path / config.CUSTOM_PAPERS_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(papers.CustomPapersError, match=message):
        papers.load_custom_for_update(tmp_path)


@pytest.mark.parametrize(
    "disabled",
    [
        "甲报",
        [1],
        [""],
        ["甲报", " 甲报 "],
    ],
)
def test_load_settings_for_update_strictly_validates_disabled(tmp_path, disabled):
    (tmp_path / config.CUSTOM_PAPERS_FILENAME).write_text(
        json.dumps({"version": 1, "papers": [], "disabled": disabled}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(papers.CustomPapersError, match="disabled"):
        papers.load_settings_for_update(tmp_path)


def test_custom_names(tmp_path):
    papers.save_custom(tmp_path, [_valid(name="甲"), _valid(name="乙")])
    assert papers.custom_names(tmp_path) == {"甲", "乙"}


# ---------------------------------------------------------------- save_custom
def test_save_custom_roundtrip(tmp_path):
    entries = [_valid(name="甲", style="dash", timeout=10, retries=1, node_exclude="/pad/")]
    papers.save_custom(tmp_path, entries)
    path = tmp_path / config.CUSTOM_PAPERS_FILENAME
    assert path.exists()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert raw["papers"] == entries
    assert papers.load_custom(tmp_path) == entries


def test_save_settings_roundtrip_and_save_custom_preserves_disabled(tmp_path):
    entries = [_valid(name="甲报", timeout=20)]
    papers.save_settings(tmp_path, entries, [" 光明日报 ", "甲报", "光明日报"])

    settings = papers.load_settings_for_update(tmp_path)
    assert settings == papers.PaperSettings(
        custom=tuple(entries),
        disabled=("光明日报", "甲报"),
    )
    raw = json.loads((tmp_path / config.CUSTOM_PAPERS_FILENAME).read_text(encoding="utf-8"))
    assert raw == {
        "version": 1,
        "papers": entries,
        "disabled": ["光明日报", "甲报"],
    }

    replacement = [_valid(name="乙报")]
    papers.save_custom(tmp_path, replacement)
    assert papers.load_settings_for_update(tmp_path) == papers.PaperSettings(
        custom=tuple(replacement),
        disabled=("光明日报", "甲报"),
    )


def test_save_custom_creates_parent_dirs(tmp_path):
    root = tmp_path / "nested" / "root"
    papers.save_custom(root, [_valid(name="甲")])
    assert (root / config.CUSTOM_PAPERS_FILENAME).exists()
