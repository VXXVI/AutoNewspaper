"""报告渲染的单元测试。"""

from __future__ import annotations

import json
from datetime import datetime

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


def test_write_md_and_html_roundtrip(tmp_path):
    md_path = tmp_path / "hits" / "r.md"
    html_path = tmp_path / "hits" / "index.html"
    report.write_md(md_path, "kw", [DATE], STATS, HITS, HITS, "增量")
    report.write_html(html_path, "kw", HITS)
    assert "标题A" in md_path.read_text(encoding="utf-8")
    assert "标题A" in html_path.read_text(encoding="utf-8")
