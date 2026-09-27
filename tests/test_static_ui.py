"""无需浏览器的前端结构与契约测试。

原则：断言「结构 / 契约」而不是整段源码字面量——这样重构（拆分模块、改动函数体）
不会误伤测试，但「入口未 import 新模块」「混入 innerHTML」「缺少看板节点」等回归仍会被拦住。
JS 行为由 tests/frontend/*.mjs（node）覆盖。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[1] / "src" / "newspaper_auto" / "static"

JS_MODULES = ("api.js", "app.js", "dom.js", "render.js", "features.js", "store.js", "format.js")


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def _all_js() -> str:
    return "\n".join(_read(name) for name in JS_MODULES if (STATIC_DIR / name).exists())


class _ElementCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.attributes_by_id: dict[str, dict[str, str | None]] = {}
        self.scripts: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        element_id = attributes.get("id")
        if element_id:
            self.attributes_by_id[element_id] = attributes
        if tag == "script":
            self.scripts.append(attributes)


def test_result_and_dashboard_views_have_shared_filter_and_status_surfaces():
    parser = _ElementCollector()
    parser.feed(_read("index.html"))

    required_ids = {
        "keyword-chips",
        "result-context",
        "rows",
        "chart",
        "layouts",
        # 图形化看板
        "dashboard-section",
        "paper-rank",
        "keyword-rank",
        "heatmap",
        "scan-history",
        "health-text",
        # 服务断开时的自助恢复入口
        "service-tip",
        "service-retry",
    }
    assert required_ids <= parser.attributes_by_id.keys()
    assert parser.attributes_by_id["result-context"].get("role") == "status"
    assert parser.attributes_by_id["result-context"].get("aria-live") == "polite"


def test_index_loads_api_then_single_es_module_entry():
    parser = _ElementCollector()
    parser.feed(_read("index.html"))

    sources = [s.get("src") for s in parser.scripts if s.get("src")]
    assert "/static/api.js" in sources
    assert "/static/app.js" in sources
    assert sources.index("/static/api.js") < sources.index("/static/app.js")
    modules = [s for s in parser.scripts if s.get("type") == "module"]
    assert [s.get("src") for s in modules] == ["/static/app.js"], "app.js 应是唯一 ES Module 入口"


def test_entry_imports_the_split_modules():
    app = _read("app.js")
    for name in ("dom.js", "render.js", "features.js", "store.js", "format.js"):
        assert re.search(rf'from\s+"\./{re.escape(name)}"', app), f"app.js 必须 import ./{name}"
    # 入口只做接线：渲染委托给 renderApp（不再内联整页渲染）。
    assert "renderApp" in app
    assert len(app.splitlines()) < 400, "app.js 应是轻量入口，渲染/控制器已拆到独立模块"


def test_new_modules_exist_and_are_substantial():
    for name in ("dom.js", "render.js", "features.js"):
        path = STATIC_DIR / name
        assert path.exists(), f"缺失模块 {name}"
        assert len(path.read_text(encoding="utf-8").splitlines()) > 50


def test_no_innerhtml_eval_or_document_write_anywhere():
    blob = _all_js() + "\n" + _read("index.html")
    for forbidden in (
        r"\.innerHTML",
        r"outerHTML",
        r"insertAdjacentHTML",
        r"document\.write",
        r"\beval\s*\(",
        r"new\s+Function\s*\(",
    ):
        assert not re.search(forbidden, blob), f"前端出现被禁用的危险 API：{forbidden}"


def test_render_module_owns_the_shared_result_pipeline():
    render = _read("render.js")
    for symbol in (
        "renderApp",
        "renderStats",
        "renderChart",
        "renderLayouts",
        "renderRows",
        "renderScanPanel",
        "renderPapersPanel",
        "renderPaperRanking",
        "renderKeywordRanking",
        "renderHeatmap",
    ):
        assert re.search(rf"export\s+function\s+{symbol}\b", render), f"render.js 应导出 {symbol}"
    # 服务断连提示是 renderHealth 的内部步骤（与 renderHealth 一样不对外导出）
    assert "renderServiceTip" in render
    # 单一数据来源：渲染消费 store 的纯选择器，而不是各自重算。
    assert "selectSorted" in render


def test_store_owns_the_new_state_slices_and_pure_reducer():
    store = _read("store.js")
    for action in (
        "SCAN_SET_PAPERS",
        "SCAN_CONFIG_ONLY",
        "SCAN_TOGGLE_PAPER",
        "SCAN_INGEST",
        "PAPERS_VIEW",
        "PAPERS_STATUS",
        "PAPERS_EDITING",
        "EXPORT_BUSY",
        "EXPORT_STATUS",
        "SYSTEM_HEALTH",
        "SET_KEYWORDS",
    ):
        assert action in store, f"store.js 缺少 action {action}"
    assert "papersPanel" in store, "报社管理切片不得与 filter 的 papers Set 重名"
    assert re.search(r"export\s+function\s+reducer\b", store)
    assert re.search(r"export\s+function\s+selectFiltered\b", store)


def test_features_only_dispatch_and_call_api():
    features = _read("features.js")
    for controller in (
        "createScanController",
        "createPapersController",
        "createExportController",
        "createSystemController",
    ):
        assert re.search(rf"export\s+function\s+{controller}\b", features)
    for action in ("A.SCAN_", "A.PAPERS_", "A.EXPORT_", "A.SYSTEM_"):
        assert action in features, f"features.js 应通过 {action}* 派发 action"
    assert re.search(r'api\.post\(["\']/api/papers/status["\']', features)


def test_pct_and_snippet_guards_present():
    render = _read("render.js")
    assert re.search(r"export\s+function\s+pct\b", render)
    assert "Number.isFinite" in render
    assert re.search(r"\(r\.snippets\s*\|\|\s*\[\]\)", render)


def test_new_widget_and_legacy_tokens_are_styled():
    css = _read("style.css")
    for cls in (
        ".compact-field",
        ".empty-hint",
        ".is-running",
        ".src-builtin",  # 历史挂空 token
        ".dashboard-grid",
        ".rank-row",
        ".rank-bar",
        ".heatmap-cell",
        ".history-row",
        ".history-state",
        ".service-tip",
    ):
        assert cls in css, f"style.css 缺少 {cls} 样式"
