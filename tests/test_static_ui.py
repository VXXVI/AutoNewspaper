"""无需浏览器的前端结构与 API 契约测试。"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[1] / "src" / "newspaper_auto" / "static"


class _ElementCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.attributes_by_id: dict[str, dict[str, str | None]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        element_id = attributes.get("id")
        if element_id:
            self.attributes_by_id[element_id] = attributes


def test_result_views_have_shared_filter_and_status_surfaces():
    parser = _ElementCollector()
    parser.feed((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    required_ids = {"keyword-chips", "result-context", "rows", "chart", "layouts"}
    assert required_ids <= parser.attributes_by_id.keys()
    assert parser.attributes_by_id["result-context"].get("role") == "status"
    assert parser.attributes_by_id["result-context"].get("aria-live") == "polite"


def test_frontend_uses_paper_status_api_and_scan_keyword_focus():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert '"/api/papers/status"' in script
    assert "focusKeywords" in script


def test_trend_layout_and_rows_use_the_same_linked_result_set():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "renderChart(list);" in script
    assert "renderLayouts(list);" in script
    assert "renderRows(list);" in script
    assert "ignoreLinked" not in script
