"""无需浏览器的前端结构与契约测试。

原则：断言「结构 / 契约」而不是整段源码字面量——这样重构（拆分模块、改动函数体）
不会误伤测试，但「入口未 import 新模块」「混入 innerHTML」「缺少看板节点」等回归仍会被拦住。
JS 行为由 tests/frontend/*.mjs（node）覆盖。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import ClassVar

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
        # 结果筛选浮层：收起时零占位，整行宽度留给命中列表
        "filter-toggle",
        "filter-panel",
        "filter-count",
        # 导出对话框：目标目录可以手动填，也可以让看板所在设备弹系统窗口来选
        "export-directory",
        "export-browse",
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
        # 主题三态切换与「只看本次新增」筛选
        "theme-toggle",
        "new-only",
        "new-only-hint",
    }
    assert required_ids <= parser.attributes_by_id.keys()
    assert parser.attributes_by_id["result-context"].get("role") == "status"
    assert parser.attributes_by_id["result-context"].get("aria-live") == "polite"


def test_brand_icon_assets_are_present_and_referenced():
    """品牌图标：文件必须在、index.html 必须引用，且都进了 STATIC_TYPES 白名单。

    回归：图标是二进制资源，漏进白名单时浏览器只会拿到 404（不像 JS 会让整页报错），
    因此需要独立守护。
    """
    from newspaper_auto import web_static

    for name in ("logo.svg", "favicon.ico", "apple-touch-icon.png"):
        assert (STATIC_DIR / name).is_file(), f"缺少图标资源 static/{name}"
        assert name in web_static.STATIC_TYPES, f"static/{name} 未登记进 STATIC_TYPES"

    html = _read("index.html")
    assert 'href="/static/favicon.ico"' in html
    assert 'href="/static/apple-touch-icon.png"' in html
    assert 'src="/static/logo.svg"' in html, "顶栏品牌位应使用 logo.svg"
    # logo.svg 必须是可缩放的矢量（不锁死像素尺寸），否则高 DPI 会糊
    assert "viewBox=" in _read("logo.svg")


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


def test_theme_toggle_is_wired_and_dark_tokens_live_once():
    """主题三态：顶栏控件 + 首屏防闪脚本 + **唯一一份**深色令牌。

    深色令牌一度靠 prefers-color-scheme 复制一整套：两处色板最容易悄悄漂移。
    现在 auto 在 JS 里解析成显式值，CSS 只保留 :root[data-theme="dark"] 一份。
    """
    html = _read("index.html")
    assert 'id="theme-toggle"' in html
    for value in ("light", "dark", "auto"):
        assert f'data-theme-value="{value}"' in html, f"缺少主题按钮 {value}"
    # 首屏防闪脚本要在样式表之前跑，且与 theme.js 用同一个 localStorage 键
    assert html.index("newspaper-auto.theme") < html.index("/static/style.css")
    assert "prefers-color-scheme" in html, "内联脚本需据系统偏好解析 auto"

    css = _read("style.css")
    assert ':root[data-theme="dark"]' in css
    assert "prefers-color-scheme: dark" not in css, "深色令牌不得再有媒体查询副本"
    assert ".theme-toggle" in css
    assert ".tag-new" in css

    app = _read("app.js")
    assert re.search(r'from\s+"\./theme\.js"', app), "app.js 必须 import ./theme.js"


def test_new_only_filter_surface_is_present():
    """「只看本次新增」需要按钮、提示、状态切片与后端快照字段。"""
    html = _read("index.html")
    assert 'id="new-only"' in html
    assert 'id="new-only-hint"' in html

    store = _read("store.js")
    assert "SET_NEW_ONLY" in store
    assert "newUrlSet" in store
    assert re.search(r"new_urls", store), "筛选需消费后端下发的 new_urls"


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
        "renderRows",
        "renderScanPanel",
        "renderPapersPanel",
        "renderPaperRanking",
        "renderKeywordRanking",
        "renderHeatmap",
        "activeFilterCount",
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
        "EXPORT_PICKER",
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
    # 注：.compact-field 曾经靠一条独立的 `min-width: 0` 兜底，现已由 .field 自身的
    # min-width: 0 覆盖（那条才是移动端横向溢出的真正根因），故不再单列断言。
    for cls in (
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
        ".path-input",
    ):
        assert cls in css, f"style.css 缺少 {cls} 样式"


class _NestCollector(HTMLParser):
    """记录每个带 id 元素的「祖先链」，用于断言页面骨架（谁是谁的父级）。"""

    _VOID: ClassVar[set[str]] = {
        "meta",
        "link",
        "img",
        "br",
        "hr",
        "input",
        "source",
        "area",
        "col",
        "base",
        "wbr",
    }

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[dict] = []
        self.by_id: dict[str, dict] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        node = {"tag": tag, "attrs": attributes, "chain": list(self.stack)}
        if attributes.get("id"):
            self.by_id[attributes["id"]] = node
        if tag not in self._VOID:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                return


def test_result_row_carries_its_own_layout_map():
    """每条命中在**行内**自带版面位置缩略图，而不是页面下方另起一整栏。

    回归：这正是本次布局改动的核心契约 —— 整版位置图必须由命中行渲染出来
    （``.row-map`` 出现在 ``renderRows`` 里），页面骨架里不应再有独立的版面位置栏。
    """
    parser = _NestCollector()
    parser.feed(_read("index.html"))
    assert "layout-rail" not in parser.by_id, "独立的版面位置栏应已删除"
    assert "layouts" not in parser.by_id, "整栏的版面位置图容器应已删除"

    render = _read("render.js")
    rows_body = render.split("export function renderRows", 1)[1]
    assert "rowMap(" in rows_body, "版面位置缩略图应由 renderRows 逐行渲染"
    assert "row-map" in render, "缩略图应带 .row-map 样式钩子"
    assert "svg" in render and "createElementNS" in render, "缩略位置图应画成 SVG"
    # 位置图按版面坐标（0~1）而非像素绘制
    assert "preserveAspectRatio" in render, "缩略图必须让 x/y 各自归一化，否则版面会被画扁"
    assert "activeFilterCount" in render, "筛选按钮需要角标来体现当前筛选强度"


def test_filter_popover_is_anchored_inside_the_results_card():
    """筛选是结果卡右上角的浮层：结果表吃满整卡宽度，不再有左侧常驻栏。"""
    parser = _NestCollector()
    parser.feed(_read("index.html"))

    def classes_in_chain(node: dict) -> set[str]:
        found: set[str] = set()
        for ancestor in node["chain"]:
            found.update((ancestor["attrs"].get("class") or "").split())
        return found

    panel = parser.by_id["filter-panel"]
    trigger = parser.by_id["filter-toggle"]
    rows = parser.by_id["rows"]

    assert "filter-popover" in (panel["attrs"].get("class") or ""), "筛选面板应是浮层（.filter-popover）"
    assert "results-heading" in classes_in_chain(panel), "浮层要挂在结果卡标题上（定位锚）"
    assert "hidden" in panel["attrs"], "浮层默认收起"
    assert trigger["attrs"].get("aria-controls") == "filter-panel"
    assert trigger["attrs"].get("aria-expanded") == "false"

    # 结果表不再被左右分栏包着，直接挂在 .results-card 下
    for stale in ("results-workspace", "results-layout"):
        assert stale not in classes_in_chain(rows), f"结果列表不应再被 .{stale} 分栏"
    assert "results-card" in classes_in_chain(rows)

    # 时间趋势仍在独立的分析区
    chart = parser.by_id["chart"]
    assert parser.by_id["trend-section"] in chart["chain"]
    assert "analysis-grid" not in classes_in_chain(chart), "趋势图不必再套一格网格"


def test_row_map_and_filter_popover_are_styled():
    css = _read("style.css")
    for token in (
        ".row-map",
        ".row-map .map-hit",
        ".cell-body",
        ".filter-popover",
        ".filter-count",
        "--page-max",
    ):
        assert token in css, f"style.css 缺少布局相关样式：{token}"
    # 缩略图描边用 non-scaling-stroke：viewBox 被非等比拉伸，描边必须保持物理像素
    assert "non-scaling-stroke" in css
    for stale in (".results-layout", ".layout-rail", ".layout-item", ".filter-panel"):
        assert stale not in css, f"style.css 仍残留已删除的布局样式：{stale}"


def test_reading_results_precede_analysis_and_scan_guidance_is_compact():
    html = _read("index.html")
    sections = ["scan-card", "metrics-section", "results-card", "dashboard-section", "trend-section"]
    positions = [html.index(f'id="{section}"') for section in sections]
    assert positions == sorted(positions), "阅读结果应在分析图表之前"
    assert 'id="scan-recent"' in html, "空闲扫描区应提供最近任务摘要"
    assert 'class="run-steps"' not in html, "固定流程说明不应挤占首屏"
