"""报告渲染的单元测试。"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime

import pytest

from newspaper_auto import report

DATE = datetime(2026, 9, 22)
STATS = [{"paper": "湛江日报", "date": "2026-09-22", "sections": 3, "articles": 4, "scanned": 4, "note": ""}]
HITS = [
    {
        "paper": "湛江日报",
        "date": "2026-09-22",
        "section": "第02版：综合",
        "title": "标题A",
        "url": "http://x/2026-09/22/content_101.html",
        "text_len": 100,
        "snippets": ["…广东海洋大学…"],
        "matched": ["广东海洋大学"],
    }
]


def test_render_md_lists_new_hits_and_stats_table():
    md = report.render_md("广东海洋大学", [DATE], STATS, HITS, HITS, "增量")
    assert "## 新增命中" in md
    assert "标题A" in md
    assert "| 湛江日报 | 2026-09-22 | 3 | 4/4 | 1 |" in md


def test_render_md_without_new_hits():
    md = report.render_md("kw", [DATE], STATS, [], [], "增量")
    assert "无新增命中" in md


def test_render_md_history_section_excludes_new():
    known = {
        "paper": "湛江日报",
        "date": "2026-09-21",
        "section": "",
        "title": "旧标题",
        "url": "http://x/2026-09/21/content_9.html",
        "text_len": 10,
        "matched": ["kw"],
    }
    md = report.render_md("kw", [DATE], STATS, HITS, [*HITS, known], "增量")
    assert "其余已知命中（历史）" in md
    assert "旧标题" in md
    assert "标题A" in md.split("其余已知命中")[0]  # 新增不再重复列出


def test_hits_to_json_roundtrip():
    assert json.loads(report.hits_to_json(HITS)) == HITS


def test_hits_to_csv_header_and_semicolon_join():
    lines = report.hits_to_csv(HITS).strip().splitlines()
    assert lines[0] == "paper,date,section,title,url,text_len,matched"
    assert lines[1].endswith("广东海洋大学")
    assert len(lines) == 2


@pytest.mark.parametrize("dangerous", ["=1+1", "+cmd", "-2+3", "@SUM(A1)", "\t=1+1", "\r=1+1"])
def test_hits_to_csv_escapes_formula_prefixes(dangerous):
    hit = {**HITS[0], "title": dangerous}

    row = next(csv.DictReader(io.StringIO(report.hits_to_csv([hit]))))

    assert row["title"] == f"'{dangerous}"


def test_hits_to_csv_escapes_all_text_fields_but_preserves_numeric_text_len():
    hit = {
        "paper": "=paper",
        "date": "+date",
        "section": "-section",
        "title": "@title",
        "url": "\turl",
        "text_len": -42,
        "matched": ["\rkeyword"],
    }

    row = next(csv.DictReader(io.StringIO(report.hits_to_csv([hit]))))

    assert row == {
        "paper": "'=paper",
        "date": "'+date",
        "section": "'-section",
        "title": "'@title",
        "url": "'\turl",
        "text_len": "-42",
        "matched": "'\rkeyword",
    }


def test_render_html_escapes_untrusted_content():
    malicious = [
        {
            "paper": "<script>alert(1)</script>",
            "date": "2026-09-22",
            "section": None,
            "title": "a&b",
            "url": "http://x/?a=1&b=2",
            "snippets": ["<b>bold</b>"],
        }
    ]
    html = report.render_html("kw<>&", malicious)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "a&amp;b" in html
    assert "&lt;b&gt;bold&lt;/b&gt;" in html


def test_render_html_labels_archive_as_history_and_shows_matched_keywords():
    html = report.render_html("人工智能", HITS)

    assert "数字报历史命中汇总" in html
    assert "本次更新关键词：人工智能" in html
    assert "命中关键词" in html
    assert "广东海洋大学" in html


def test_write_md_and_html_roundtrip(tmp_path):
    md_path = tmp_path / "hits" / "r.md"
    html_path = tmp_path / "hits" / "index.html"
    report.write_md(md_path, "kw", [DATE], STATS, HITS, HITS, "增量")
    report.write_html(html_path, "kw", HITS)
    assert "标题A" in md_path.read_text(encoding="utf-8")
    assert "标题A" in html_path.read_text(encoding="utf-8")
    assert list((tmp_path / "hits").glob("*.tmp")) == []


def test_hits_to_csv_escapes_non_numeric_text_len_only():
    """text_len 是数值时原样输出（负数不能被误加撇号），是字符串时才走公式转义。"""
    numeric = {**HITS[0], "text_len": -42}
    texty = {**HITS[0], "text_len": "=1+1"}

    rows = list(csv.DictReader(io.StringIO(report.hits_to_csv([numeric, texty]))))

    assert rows[0]["text_len"] == "-42"
    assert rows[1]["text_len"] == "'=1+1"


def test_render_md_lists_failed_entries():
    """「抓取失败（缺报）」章节必须渲染出来，否则「无命中」会被误读成「真的没有」。"""
    stats = [
        {
            **STATS[0],
            "sections_failed": 2,
            "articles_failed": 1,
            "failed_urls": [{"code": 404, "url": "http://x/miss.html", "reason": "not found"}],
        },
        {
            "paper": "南方日报",
            "date": "2026-09-22",
            "sections": 1,
            "articles": 0,
            "scanned": 0,
            "ok": False,
            "entry_code": 503,
            "note": "入口请求失败",
        },
    ]

    md = report.render_md("kw", [DATE], stats, [], [], "增量")

    assert "## 抓取失败（缺报）" in md
    assert "版面页失败 2 个" in md
    assert "文章页失败 1 篇" in md
    assert "code=404 http://x/miss.html" in md
    assert "南方日报" in md
    assert "code=503" in md


def test_render_html_links_only_http_and_https():
    """非 http/https 的 URL（data: / javascript:）必须降级为纯文本，不得进入 href。"""
    archive = [
        {**HITS[0], "url": "http://x/ok.html"},
        {**HITS[0], "title": "危险条目", "url": "data:text/html,<script>alert(1)</script>"},
    ]

    html = report.render_html("kw", archive)

    assert '<a href="http://x/ok.html"' in html
    assert 'href="data:' not in html
    assert "危险条目" in html


# ---------------------------------------------------------------- 任务 B：版内位置
GEOM_HIT = {
    **HITS[0],
    "section_no": "第02版",
    "geometry": {
        "x1": 2,
        "y1": 100,
        "x2": 336,
        "y2": 300,
        "top": 0.38,
        "bottom": 0.9,
        "index": 2,
        "total": 7,
    },
}


def test_geometry_label_formats_and_degrades():
    assert report.geometry_label(GEOM_HIT["geometry"]) == "版内 2/7 · 上版 38%"
    assert report.geometry_label({"index": 1, "total": 3, "top": 0.5}) == "版内 1/3 · 上版 50%"
    assert report.geometry_label({"top": 0.5}) == "上版 50%"
    # 缺失/异常一律空串，绝不抛异常（老归档向后兼容）
    assert report.geometry_label(None) == ""
    assert report.geometry_label({}) == ""
    assert report.geometry_label("nope") == ""


def test_geometry_label_order_fallback_omits_top():
    """顺序兜底只有 index/total → 只渲染「版内 k/m」，绝不带「上版」后缀。"""
    assert report.geometry_label({"index": 3, "total": 9, "basis": "order"}) == "版内 3/9"
    assert report.geometry_label({"index": 1, "total": 1, "basis": "order"}) == "版内 1/1"
    # 含 top 的 area 形态仍带「上版 xx%」
    assert (
        report.geometry_label({"index": 3, "total": 9, "top": 0.25, "basis": "area"}) == "版内 3/9 · 上版 25%"
    )


def test_render_md_includes_area_position_for_new_hits():
    md = report.render_md("kw", [DATE], STATS, [GEOM_HIT], [GEOM_HIT], "增量")
    assert "版内位置：版内 2/7 · 上版 38%" in md


def test_render_md_without_geometry_is_backward_compatible():
    md = report.render_md("kw", [DATE], STATS, HITS, HITS, "增量")
    assert "版内位置" not in md


def test_render_html_includes_area_position():
    html = report.render_html("kw", [GEOM_HIT])
    assert "版内 2/7 · 上版 38%" in html


def test_render_html_without_geometry_is_backward_compatible():
    html = report.render_html("kw", HITS)
    assert "版内" not in html
