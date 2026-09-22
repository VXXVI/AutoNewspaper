"""HTML 解析与关键词匹配的单元测试（纯函数，无 I/O）。"""

from __future__ import annotations

import re
from datetime import datetime

import pytest

from newspaper_auto import parse


# ---------------------------------------------------------------- clean
def test_clean_removes_tags_scripts_and_entities():
    html = "<script>var x = 1;</script><style>a{color:red}</style><p>你好&nbsp;世界</p>"
    assert parse.clean(html) == "你好 世界"


def test_clean_collapses_whitespace_and_fullwidth_space():
    assert parse.clean("广东\u3000 \n\t海洋") == "广东 海洋"


def test_clean_empty():
    assert parse.clean("") == ""


# ---------------------------------------------------------------- tidy_title
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("羊城晚报 - 今日要闻", "今日要闻"),
        ("湛江日报数字报 - 第02版", "第02版"),
        ("标题 _·　", "标题"),
    ],
)
def test_tidy_title_strips_site_junk(raw, expected):
    assert parse.tidy_title(raw) == expected


# ---------------------------------------------------------------- links
def test_links_filters_absolutizes_and_dedupes():
    html = (
        '<a href="node_2.htm">a</a>'
        '<a href="./node_3.htm">b</a>'
        '<a href="node_2.htm">dup</a>'
        '<a href="javascript:void(0)">c</a>'
        '<a href="other.html">d</a>'
        '<a href="node_4.htm?x=1#frag">e</a>'
    )
    base = "http://example.com/html/2026-09/22/"
    assert parse.links(html, base, r"node_\w+\.html?$") == [
        "http://example.com/html/2026-09/22/node_2.htm",
        "http://example.com/html/2026-09/22/node_3.htm",
        "http://example.com/html/2026-09/22/node_4.htm",
    ]


def test_links_keeps_absolute_urls():
    html = '<a href="http://other.example.com/a/content_1.html">x</a>'
    assert parse.links(html, "http://base/", r"content_\w+\.html?$") == [
        "http://other.example.com/a/content_1.html",
    ]


# ---------------------------------------------------------------- title
def test_extract_title_prefers_h1():
    html = "<title>站点标题</title><h1>真正的文章标题</h1>"
    assert parse.extract_title(html) == "真正的文章标题"


def test_extract_title_falls_back_to_title_and_cleans_junk():
    html = "<title>湛江日报数字报 - 湛江日报</title>"
    assert parse.extract_title(html) == "湛江日报"


def test_extract_title_ignores_too_short_h1():
    html = "<title>完整标题就在这里</title><h1>短</h1>"
    assert parse.extract_title(html) == "完整标题就在这里"


def test_extract_title_missing():
    assert parse.extract_title("<div>无标题</div>") == ""


# ---------------------------------------------------------------- text
def test_extract_text_prefers_founder_content():
    html = "<body><p>导航</p><founder-content><p>真正的正文</p></founder-content></body>"
    assert parse.extract_text(html) == "真正的正文"


def test_extract_text_falls_back_to_body():
    html = "<html><head><title>t</title></head><body><div>正文内容</div></body></html>"
    assert parse.extract_text(html) == "正文内容"


def test_extract_text_falls_back_to_whole_html():
    assert parse.extract_text("<p>裸片段</p>") == "裸片段"


# ---------------------------------------------------------------- keyword
def test_make_kw_re_tolerates_tag_split_whitespace():
    assert parse.make_kw_re("广东海洋大学").search("来自广东 海洋 大学的专家")


def test_make_kw_re_is_case_insensitive():
    assert parse.make_kw_re("gdou").search("GDOU 校友")


def test_make_kw_re_does_not_match_other_schools():
    assert parse.make_kw_re("广东海洋大学").search("广东林业大学") is None


# ---------------------------------------------------------------- snippet
def test_snippet_adds_ellipsis_on_both_sides():
    text = "a" * 100 + "KEY" + "b" * 100
    match = re.search("KEY", text)
    result = parse.snippet(text, match, span=10)
    assert result.startswith("…") and result.endswith("…")
    assert "KEY" in result


def test_snippet_without_ellipsis_when_short():
    match = re.search("KEY", "KEY")
    assert parse.snippet("KEY", match) == "KEY"


# ---------------------------------------------------------------- date_path
@pytest.mark.parametrize("style,expected", [("ymd", "202609/22"), ("dash", "2026-09/22")])
def test_date_path(style, expected):
    assert parse.date_path(datetime(2026, 9, 22), style) == expected
