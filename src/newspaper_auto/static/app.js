/* 数字报关键词监测 · 本地看板（ES Module 入口）
 *
 * 纯原生 JS：无框架、无构建、无 CDN、零 innerHTML（数据一律 textContent +
 * createElement）。这里只做「启动 + 事件接线 + dispatch」：
 *   - 状态与纯选择器：./store.js
 *   - DOM 小工具：./dom.js
 *   - 渲染：./render.js
 *   - 控制器（扫描/报社/导出/系统）：./features.js
 * 所有 dispatch 都经 subscribe → 防抖 render 统一渲染；事件处理器不再各自 render。
 */
import { A, createInitialState, createStore, reducer } from "./store.js";
import { $ } from "./dom.js";
import { initTheme } from "./theme.js";
import { renderApp } from "./render.js";
import {
  PAPERS_COMMIT_CMD,
  createExportController,
  createPapersController,
  createScanController,
  createSystemController,
} from "./features.js";
import { shiftDate, todayIso } from "./format.js";

(function () {
  "use strict";

  var api = window.NewspaperApi;
  var loadRequestId = 0;
  var renderTimer = null;

  // 唯一 store：所有状态变更都经 dispatch(action) → reducer → 新 state。
  var store = createStore(reducer, createInitialState());

  // 唯一渲染触发点：任何 action 之后都防抖重渲染。
  store.subscribe(scheduleRender);

  function scheduleRender() {
    if (renderTimer) window.clearTimeout(renderTimer);
    renderTimer = window.setTimeout(function () {
      renderTimer = null;
      renderApp(ctx);
    }, 140);
  }

  function render() {
    if (renderTimer) { window.clearTimeout(renderTimer); renderTimer = null; }
    renderApp(ctx);
  }

  // 事件处理器只描述「发生了什么」；immediate 仅用于需要同步反馈的交互。
  function dispatch(action, options) {
    store.dispatch(action);
    if (options && options.immediate) render();
  }

  var ctx = {
    getState: store.getState,
    dispatch: dispatch,
    api: api,
    load: load
  };

  var scan = createScanController(ctx);
  var papers = createPapersController(ctx);
  var exporter = createExportController(ctx);
  var system = createSystemController(ctx);
  ctx.features = { scan: scan, papers: papers, exporter: exporter, system: system };

  /* -------------------------------------------------------------- 数据 */
  function load(options) {
    options = options || {};
    var requestId = ++loadRequestId;
    var current = store.getState();
    var hadData = Boolean(current.data);
    var allPapersSelected = hadData && current.papers.size === current.data.papers.length;
    var allKeywordsSelected = hadData && current.keywords.size === current.data.keywords.length;

    dispatch({ type: A.SYSTEM_ERROR, message: "" });
    dispatch({ type: A.SYSTEM_LOADING, loading: true });

    api.get("/api/hits")
      .then(function (data) {
        if (requestId !== loadRequestId) return;
        var requestedFocus = Array.isArray(options.focusKeywords) && options.focusKeywords.length
          ? options.focusKeywords
          : (!hadData && Array.isArray(data.configured_keywords) && data.configured_keywords.length
            ? data.configured_keywords : null);
        var resetView = !hadData || !options.preserve || Boolean(requestedFocus);
        dispatch({
          type: A.LOAD_DATA,
          data: data,
          mode: resetView ? "reset" : "preserve",
          requestedFocus: requestedFocus,
          allPapersSelected: allPapersSelected,
          allKeywordsSelected: allKeywordsSelected
        });
        dispatch({ type: A.SYSTEM_LOADING, loading: false });
      })
      .catch(function (err) {
        if (requestId !== loadRequestId) return;
        dispatch({ type: A.SYSTEM_LOADING, loading: false });
        dispatch({ type: A.SYSTEM_ERROR, message: err.message });
      });
  }

  /* -------------------------------------------------------------- 日期快捷区间 */
  function dateBounds() {
    var data = store.getState().data;
    if (!data) return ["", ""];
    var ds = Object.keys(data.date_counts).sort();
    return ds.length ? [ds[0], ds[ds.length - 1]] : ["", ""];
  }

  // 「今天」优先取服务端下发值（应用时区，见后端 clock），未加载时回落浏览器本地日期：
  // 服务端跑在 UTC 的 VPS 上时，两边对「今天」的判定必须一致，否则会把今天当成未来。
  function serverToday() {
    var data = store.getState().data;
    return (data && data.today) || todayIso();
  }

  // 以「数据里最新的日期」为基准，避免用今天算导致历史数据筛不出东西。
  function applyPreset(kind) {
    var action = { type: A.SET_PRESET, preset: kind };
    if (store.getState().data) {
      var latest = dateBounds()[1];
      if (kind === "all") { action.from = ""; action.to = ""; }
      else if (latest && kind === "today") { action.from = latest; action.to = latest; }
      else if (latest && kind === "7d") { action.from = shiftDate(latest, -6); action.to = latest; }
      else if (latest && kind === "30d") { action.from = shiftDate(latest, -29); action.to = latest; }
    }
    dispatch(action);
  }

  /* -------------------------------------------------------------- 筛选浮层开关 */
  // 记忆「筛选浮层是否展开」：localStorage 在隐私模式/被禁用时会抛异常，一律吞掉并回落到默认收起。
  var FILTERS_KEY = "newspaper-auto.filtersOpen";

  function readFiltersOpen() {
    try {
      var raw = window.localStorage.getItem(FILTERS_KEY);
      return raw === "1";
    } catch (err) { return false; }
  }

  function writeFiltersOpen(open) {
    try { window.localStorage.setItem(FILTERS_KEY, open ? "1" : "0"); } catch (err) { /* 忽略 */ }
  }

  function setFiltersOpen(open) {
    dispatch({ type: A.SET_FILTERS_OPEN, open: open }, { immediate: true });
    writeFiltersOpen(open);
  }

  function filtersOpen() { return store.getState().filtersOpen === true; }

  /* -------------------------------------------------------------- 顶部导航高亮
   * 滚动与点击锚点跟随：当用户滚动或点击顶栏跳转时，高亮当前正在阅读的区块。
   * 采用 RAF 节流的滚动位置判定（相比单纯的 IntersectionObserver，能天然处理
   * 页面底端区块高度不足以顶到视口上方、以及大卡片跨屏覆盖的边界情况）。 */
  function watchSections() {
    var nav = $("page-nav");
    if (!nav) return;
    var links = Array.prototype.slice.call(nav.querySelectorAll("a[href^='#']"));
    if (!links.length) return;
    var byId = {};
    var sections = [];
    links.forEach(function (link) {
      var id = link.getAttribute("href").slice(1);
      var target = document.getElementById(id);
      if (!target) return;
      byId[id] = link;
      sections.push({ id: id, el: target });
    });
    if (!sections.length) return;

    var ticking = false;
    function updateActive() {
      ticking = false;
      var scrollY = window.pageYOffset || document.documentElement.scrollTop || 0;
      var scrollHeight = document.documentElement.scrollHeight || document.body.scrollHeight;
      var clientHeight = window.innerHeight || document.documentElement.clientHeight;
      var isBottom = (scrollY + clientHeight) >= (scrollHeight - 40);

      var activeId = sections[0].id;
      if (isBottom) {
        // 触底时，直接高亮最后一个可见的区块（避免底端区块高度小顶不到上方而无法点亮）
        activeId = sections[sections.length - 1].id;
      } else {
        // 顶部预留导航栏净空高度（约 110px）
        var targetLine = 110;
        for (var i = 0; i < sections.length; i++) {
          var rect = sections[i].el.getBoundingClientRect();
          if (rect.top <= targetLine) {
            activeId = sections[i].id;
          } else {
            break;
          }
        }
      }

      sections.forEach(function (sec) {
        if (sec.id === activeId) byId[sec.id].setAttribute("aria-current", "true");
        else byId[sec.id].removeAttribute("aria-current");
      });
    }

    function onScroll() {
      if (!ticking) {
        ticking = true;
        window.requestAnimationFrame(updateActive);
      }
    }

    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });
    // 点击导航锚点时平滑滚动，滚动过程中实时更新高亮，保证点击与视觉无延迟对齐
    links.forEach(function (link) {
      link.addEventListener("click", function () {
        var id = link.getAttribute("href").slice(1);
        sections.forEach(function (sec) {
          if (sec.id === id) byId[sec.id].setAttribute("aria-current", "true");
          else byId[sec.id].removeAttribute("aria-current");
        });
      });
    });
    updateActive();
  }

  /* -------------------------------------------------------------- 启动 */
  function init() {
    initTheme();
    dispatch({ type: A.SET_FILTERS_OPEN, open: readFiltersOpen() });
    watchSections();
    $("filter-toggle").addEventListener("click", function () { setFiltersOpen(!filtersOpen()); });
    // 浮层的两条退出口：Esc 与点面板外部。关闭后焦点回到触发按钮，键盘用户不会丢位置。
    // 初始化日期控件的最大允许日期为「今天」（未来报纸尚未发行），口径同服务端
    var todayStr = serverToday();
    ["scan-since", "scan-until", "from", "to"].forEach(function (id) {
      var el = $(id);
      if (el) el.max = todayStr;
    });
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape" || !filtersOpen()) return;
      setFiltersOpen(false);
      $("filter-toggle").focus();
    });
    document.addEventListener("click", function (event) {
      if (!filtersOpen()) return;
      var panel = $("filter-panel"), trigger = $("filter-toggle");
      if (panel.contains(event.target) || trigger.contains(event.target)) return;
      setFiltersOpen(false);
    });
    $("reload").addEventListener("click", function () { load({ preserve: true }); });
    $("scan-form").addEventListener("submit", function (event) {
      event.preventDefault();
      if (!store.getState().scan.running) scan.start();
    });
    $("scan-mode").addEventListener("change", scan.onModeChange);
    $("scan-since").addEventListener("change", function (event) {
      var today = serverToday();
      if (event.target.value && event.target.value > today) event.target.value = today;
    });
    $("scan-until").addEventListener("change", function (event) {
      var today = serverToday();
      if (event.target.value && event.target.value > today) event.target.value = today;
    });
    if ($("scan-cancel")) $("scan-cancel").addEventListener("click", scan.cancel);

    $("paper-add").addEventListener("click", papers.add);
    $("paper-create").addEventListener("click", papers.showCreate);
    $("paper-test").addEventListener("click", papers.testForm);
    $("paper-clear").addEventListener("click", papers.clearForm);
    $("paper-style").addEventListener("change", papers.updateStyleUI);
    $("papers-commit-cmd").value = PAPERS_COMMIT_CMD;
    $("papers-commit").addEventListener("click", papers.copyCommitCommand);

    $("result-clear-linked").addEventListener("click", function () {
      dispatch({ type: A.CLEAR_LINKED }, { immediate: true });
    });
    $("result-show-history").addEventListener("click", function () {
      dispatch({ type: A.SHOW_ALL_HISTORY });
    });

    $("q").addEventListener("input", function (event) {
      dispatch({ type: A.SET_QUERY, value: event.target.value });
    });
    $("from").addEventListener("change", function (event) {
      var val = event.target.value;
      var today = serverToday();
      if (val && val > today) {
        val = today;
        event.target.value = today;
      }
      dispatch({ type: A.SET_DATE_RANGE, from: val });
    });
    $("to").addEventListener("change", function (event) {
      var val = event.target.value;
      var today = serverToday();
      if (val && val > today) {
        val = today;
        event.target.value = today;
      }
      dispatch({ type: A.SET_DATE_RANGE, to: val });
    });
    $("sort").addEventListener("change", function (event) {
      dispatch({ type: A.SET_SORT, value: event.target.value }, { immediate: true });
    });

    $("export").addEventListener("click", exporter.open);
    $("export-form").addEventListener("submit", exporter.save);
    $("export-close").addEventListener("click", exporter.close);
    $("export-cancel").addEventListener("click", exporter.close);
    $("export-browser").addEventListener("click", exporter.fallback);
    $("export-browse").addEventListener("click", exporter.browse);
    $("export-directory").addEventListener("input", exporter.onDirectoryInput);
    $("export-dialog").addEventListener("cancel", function (event) {
      if (store.getState().exportDialog.busy) event.preventDefault();
    });
    $("export-dialog").addEventListener("click", function (event) {
      if (event.target === $("export-dialog")) exporter.close();
    });

    $("results-more").addEventListener("click", function () {
      dispatch({ type: A.LOAD_MORE }, { immediate: true });
    });
    Array.prototype.forEach.call($("preset-chips").querySelectorAll("[data-preset]"), function (btn) {
      btn.addEventListener("click", function () { applyPreset(btn.getAttribute("data-preset")); });
    });
    $("new-only").addEventListener("click", function () {
      dispatch({ type: A.SET_NEW_ONLY, on: !store.getState().newOnly });
    });
    $("reset").addEventListener("click", function () {
      dispatch({ type: A.RESET_VIEW });
    });
    if ($("service-retry")) $("service-retry").addEventListener("click", system.reconnect);

    system.loadHealth();
    system.loadScanHistory();
    scan.loadConfig();
    papers.load();
    scan.resume();
    load();
  }

  init();
})();
