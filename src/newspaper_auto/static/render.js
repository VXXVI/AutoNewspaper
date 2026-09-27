/* render.js —— 所有「state → DOM」的渲染都在这里；事件只 dispatch，绝不先改 DOM 再发 action。
 * 零 innerHTML：一律 el()/mount()/textContent 建节点；chip/柱/热力格的 on 与 aria-pressed 由 state 推导。
 */
import {
  A, selectFiltered, selectSorted, selectScanNames,
  paperRanking, keywordRanking, heatmap, HEATMAP_KEY_SEP,
} from "./store.js";
import { $, el, mount, safeUrl, setText, setInputValue, clearNode, setHidden, chip } from "./dom.js";
import { geomText } from "./format.js";

export var MAX_LAYOUTS = 24;
export var MAX_RANK = 8;
export var MAX_HEATMAP_PAPERS = 8;
export var MAX_HEATMAP_DATES = 14;
export var MAX_HISTORY = 8;

export function pct(value) {
  if (!Number.isFinite(value)) return "0%";
  return Math.max(0, Math.min(100, value * 100)).toFixed(2) + "%";
}
function emptyHint(box, text) {
  mount(box, "p", "empty-hint", text).setAttribute("role", "status");
}
function toggleLinked(ctx, filter) {
  var current = ctx.getState().linkedFilter;
  var same = current && current.type === filter.type && current.key === filter.key;
  ctx.dispatch({ type: A.SET_LINKED, filter: same ? null : filter }, { immediate: true });
  var card = $("results-card");
  if (card && card.scrollIntoView) card.scrollIntoView({ behavior: "smooth", block: "start" });
}

export function renderApp(ctx) {
  var state = ctx.getState();
  renderMeta(state);
  renderHealth(state);
  renderVersion(state);
  renderFilterInputs(state);
  renderDateBounds(state);
  renderControls(ctx, state);
  renderScanPanel(state);
  renderScanHistory(state);
  renderPapersPanel(ctx, state);
  if (state.data) {
    var all = state.data.records;
    var list = selectSorted(state);
    renderStats(state, all, list);
    renderChart(ctx, list);
    renderLayouts(ctx, list);
    renderRows(state, list);
    renderResultContext(state, list);
    renderShown(state, all, list);
    renderEmpty(state, all, list);
    renderRangeHint(state);
    renderPaperRanking(state);
    renderKeywordRanking(ctx, list);
    renderHeatmap(ctx, list);
    renderFooter(state);
  }
  renderExportPanel(state);
}

function renderMeta(state) {
  var meta = $("meta");
  if (!meta) return;
  if (state.system.error) return setText(meta, "加载失败：" + state.system.error);
  if (!state.data) return setText(meta, state.system.loading ? "加载中…" : "正在读取数据…");
  var d = state.data;
  setText(meta, "共 " + d.records.length + " 条命中 · 涉及 " + d.papers.length +
    " 家报社 · 数据更新于 " + d.generated_at + (state.system.loading ? " · 正在刷新…" : ""));
}
function renderHealth(state) {
  var box = $("health"), text = $("health-text");
  if (!box || !text) return;
  if (state.system.ok === true) {
    setText(text, "服务正常 · v" + (state.system.version || (state.data && state.data.version) || "?"));
    box.setAttribute("data-state", "ok");
  } else if (state.system.ok === false) {
    setText(text, "服务离线");
    box.setAttribute("data-state", "off");
  } else {
    setText(text, "连接中…");
    box.setAttribute("data-state", "unknown");
  }
  if (state.data && state.data.generated_at) box.title = "数据生成时间：" + state.data.generated_at;
}
function renderVersion(state) { setText($("version"), state.data ? "v" + state.data.version : ""); }
function renderFilterInputs(state) {
  setInputValue($("q"), state.q);
  setInputValue($("from"), state.from);
  setInputValue($("to"), state.to);
  var sort = $("sort");
  if (sort && sort.value !== state.sort) sort.value = state.sort;
}
function renderDateBounds(state) {
  if (!state.data) return;
  var dates = Object.keys(state.data.date_counts).sort();
  var min = dates.length ? dates[0] : "", max = dates.length ? dates[dates.length - 1] : "";
  ["from", "to"].forEach(function (id) { var input = $(id); if (input) { input.min = min; input.max = max; } });
}

/* ------------------------------------------------------- 筛选 / 扫描 chips */
export function renderControls(ctx, state) {
  var data = state.data;
  var paperBox = $("paper-chips");
  if (paperBox) {
    clearNode(paperBox);
    (data ? data.papers : []).forEach(function (name) {
      paperBox.appendChild(chip({
        label: name, on: state.papers.has(name),
        onClick: function () {
          ctx.dispatch({ type: A.TOGGLE_PAPER, name: name, on: !state.papers.has(name) });
          ctx.dispatch({ type: A.CLEAR_LINKED });
        }
      }));
    });
  }
  var kwBox = $("keyword-chips");
  if (kwBox) {
    clearNode(kwBox);
    var names = data ? data.keywords.slice() : [];
    (state.focusKeywords || []).forEach(function (kw) { if (names.indexOf(kw) === -1) names.unshift(kw); });
    names.forEach(function (kw) {
      kwBox.appendChild(chip({
        label: kw, on: state.keywords.has(kw),
        onClick: function () {
          ctx.dispatch({ type: A.TOGGLE_KEYWORD, name: kw, on: !state.keywords.has(kw) });
          ctx.dispatch({ type: A.CLEAR_LINKED });
        }
      }));
    });
  }
  var presetBox = $("preset-chips");
  if (presetBox) {
    Array.prototype.forEach.call(presetBox.querySelectorAll("[data-preset]"), function (btn) {
      var active = btn.getAttribute("data-preset") === state.preset;
      btn.classList.toggle("on", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }
  var scanBox = $("scan-papers");
  if (scanBox) {
    clearNode(scanBox);
    state.scan.paperNames.forEach(function (name) {
      var on = !state.scan.selected || state.scan.selected.has(name);
      scanBox.appendChild(chip({
        label: name, on: on, ariaLabel: (on ? "取消选择" : "选择") + name,
        onClick: function () { ctx.dispatch({ type: A.SCAN_TOGGLE_PAPER, name: name, on: !on }); }
      }));
    });
  }
}

/* ------------------------------------------------------- 扫描面板 */
export function renderScanPanel(state) {
  var scan = state.scan;
  var card = $("scan-card");
  if (card) card.setAttribute("aria-busy", scan.running ? "true" : "false");
  var status = $("scan-status");
  if (status) { setText(status, scan.status.text); status.setAttribute("data-state", scan.status.tone); }
  var selectedCount = selectScanNames(scan).length;
  var zero = scan.paperNames.length === 0 || selectedCount === 0;
  var run = $("scan-run");
  if (run) {
    run.disabled = scan.running || zero;
    var label = run.querySelector("span:last-child");
    if (label) setText(label, scan.running ? "扫描中…" : "开始扫描");
    else setText(run, scan.running ? "扫描中…" : "开始扫描");
    run.classList.toggle("is-running", scan.running);
  }
  var cancel = $("scan-cancel");
  if (cancel) { cancel.hidden = !scan.running; cancel.disabled = scan.cancelling; }
  var hint = $("scan-papers-hint");
  if (hint) {
    if (!scan.paperNames.length) setText(hint, "⚠ 没有已启用的报社，请先在下方恢复或添加");
    else if (selectedCount === 0) setText(hint, "⚠ 至少选择一家报社才能扫描");
    else setText(hint, "已选 " + selectedCount + " / " + scan.paperNames.length + " 家");
  }
  renderScanProgress(scan.progress);
  var log = $("scan-log");
  if (log) { setText(log, scan.logLines.join("\n")); log.scrollTop = log.scrollHeight; }
}
function renderScanProgress(progress) {
  var wrap = $("scan-progress"), bar = $("scan-progress-bar"), text = $("scan-progress-text");
  if (!wrap) return;
  if (!progress) {
    wrap.hidden = true;
    if (bar) bar.style.width = "0%";
    setText(text, "正在准备…");
    wrap.setAttribute("aria-valuenow", "0");
    return;
  }
  var percent = Number.isFinite(progress.percent) ? progress.percent : 0;
  wrap.hidden = false;
  if (bar) bar.style.width = Math.max(0, Math.min(100, percent)) + "%";
  wrap.setAttribute("aria-valuenow", String(percent));
  setText(text, progress.total ? progress.completed + " / " + progress.total + "（" + percent + "%）" : "正在扫描…");
}

var HISTORY_STATUS = {
  done: ["完成", "success"], error: ["未完整", "error"], cancelled: ["已取消", "idle"],
  running: ["进行中", "running"], idle: ["准备", "idle"],
};
export function renderScanHistory(state) {
  var box = $("scan-history");
  if (!box) return;
  clearNode(box);
  var history = state.scan.history;
  if (history === null) return emptyHint(box, "正在加载扫描历史…");
  if (!history.length) return emptyHint(box, "暂无扫描历史记录。");
  history.slice().reverse().slice(0, MAX_HISTORY).forEach(function (task) {
    var meta = HISTORY_STATUS[task.status] || ["未知", "idle"];
    var hits = task.summary && typeof task.summary.hits === "number" ? task.summary.hits : null;
    var when = task.finished_at || task.started_at || "—";
    var row = mount(box, "article", "history-row");
    row.setAttribute("aria-label", meta[0] + " " + when + "，命中 " + (hits === null ? "未知" : hits) + " 篇");
    var head = mount(row, "div", "history-head");
    mount(head, "span", "history-state tone-" + meta[1], meta[0]);
    mount(head, "span", "history-time", when);
    mount(row, "p", "history-meta", ((task.keywords || []).join("、") || "（未记录关键词）") +
      (task.only ? " · 报社：" + task.only : ""));
    var detail = hits === null ? "命中未知" : "命中 " + hits + " 篇";
    if (typeof task.duration_seconds === "number") detail += " · 用时 " + task.duration_seconds + "s";
    else if (typeof task.exit_code === "number") detail += " · 退出码 " + task.exit_code;
    mount(row, "p", "history-detail", detail);
  });
}

/* ------------------------------------------------------- 报社列表 */
export function renderPapersPanel(ctx, state) {
  var view = state.papersPanel.view;
  var box = $("papers-list");
  if (box) {
    clearNode(box);
    var items = view && Array.isArray(view.items) ? view.items : [];
    if (!items.length) mount(box, "span", "hint", view ? "暂无报社" : "正在加载报社…");
    else {
      var sourceLabels = { builtin: "内置", custom: "自定义", override: "已覆盖" };
      items.forEach(function (item) {
        var name = item.name, enabled = item.enabled !== false;
        var row = mount(box, "div", enabled ? "papers-row" : "papers-row is-disabled");
        row.setAttribute("data-paper-name", name);
        mount(row, "span", "paper-name", name);
        mount(row, "span", "paper-src " + (item.source === "builtin" ? "src-builtin" : "src-custom"),
          sourceLabels[item.source] || "自定义");
        mount(row, "span", enabled ? "paper-state is-on" : "paper-state", enabled ? "已启用" : "已停用");
        var actions = mount(row, "span", "paper-actions");
        paperButton(actions, "试抓", "试抓" + name, function () {
          ctx.features.papers.runTest(item.config || { name: name });
        });
        paperButton(actions, item.source === "builtin" ? "修改规则" : "编辑", "编辑" + name + "抓取规则", function () {
          ctx.features.papers.fillForm(item.config || {}, item.source);
        });
        var toggleBtn = paperButton(actions, enabled ? "停用" : "恢复", (enabled ? "停用" : "恢复") + name,
          function () { ctx.features.papers.setEnabled(name, !enabled, row, toggleBtn); },
          enabled ? "chip danger" : "chip enable");
        toggleBtn.setAttribute("role", "switch");
        toggleBtn.setAttribute("aria-checked", enabled ? "true" : "false");
        if (item.source === "custom" || item.source === "override") {
          var deleteLabel = item.source === "override" ? "恢复默认" : "删除";
          paperButton(actions, deleteLabel, deleteLabel + name, function () {
            ctx.features.papers.deletePaper(name, item.source);
          }, item.source === "override" ? "chip" : "chip danger");
        }
      });
    }
  }
  setText($("papers-status"), state.papersPanel.status);
  var feedback = $("papers-feedback");
  if (feedback) {
    setText(feedback, state.papersPanel.status === "—" ? "" : state.papersPanel.status);
    feedback.setAttribute("data-state", state.papersPanel.tone);
  }
  setText($("papers-hint"), state.papersPanel.hint || "试抓只检查网页结构，不抓正文、不写入结果。");
  var commit = $("papers-commit-row");
  if (commit) commit.hidden = !(view && ((view.custom && view.custom.length) || (view.disabled && view.disabled.length)));
}
function paperButton(parent, label, ariaLabel, onClick, className) {
  var btn = mount(parent, "button", className || "chip", label);
  btn.type = "button";
  btn.setAttribute("aria-label", ariaLabel);
  btn.addEventListener("click", onClick);
  return btn;
}

/* ------------------------------------------------------- 概览 / 图表 / 列表 */
export function renderStats(state, all, list) {
  var data = state.data;
  var seenTotal = Object.keys(data.seen_counts).reduce(function (n, k) { return n + data.seen_counts[k]; }, 0);
  var dates = Array.from(new Set(list.map(function (r) { return r.date; }))).sort();
  var papers = Array.from(new Set(list.map(function (r) { return r.paper; })));
  var cards = [
    ["全部归档", all.length, "历史命中报道"],
    ["当前结果", list.length, list.length === all.length ? "未使用筛选" : "从 " + all.length + " 条中筛出"],
    ["相关报社", papers.length, papers.join("、") || "暂无报社"],
    ["覆盖日期", dates.length, dates.length ? dates[0] + " 至 " + dates[dates.length - 1] : "暂无日期"],
    ["历史已检索", seenTotal, "所有关键词累计，用于识别新增"],
  ];
  var box = $("stats");
  if (!box) return;
  clearNode(box);
  cards.forEach(function (item) {
    var card = mount(box, "article", "stat");
    card.setAttribute("aria-label", item[0] + "：" + item[1] + (item[2] ? "，" + item[2] : ""));
    mount(card, "div", "k", item[0]);
    mount(card, "div", "v", item[1]);
    if (item[2]) { var detail = mount(card, "div", "x", item[2]); detail.title = item[2]; }
  });
}

export function renderChart(ctx, records) {
  var box = $("chart");
  if (!box) return;
  clearNode(box);
  var counts = {};
  records.forEach(function (r) { counts[r.date] = (counts[r.date] || 0) + 1; });
  var dates = Object.keys(counts).sort();
  if (!dates.length) {
    box.setAttribute("aria-label", "每日命中数量趋势图，暂无数据");
    mount(box, "div", "hint", "暂无数据");
    return;
  }
  if (dates.length > 90) dates = dates.slice(dates.length - 90);
  var max = dates.reduce(function (m, d) { return Math.max(m, counts[d]); }, 1);
  var total = dates.reduce(function (s, d) { return s + counts[d]; }, 0);
  box.setAttribute("aria-label", "每日命中数量趋势图，从 " + dates[0] + " 到 " +
    dates[dates.length - 1] + "，共 " + total + " 条，单日最高 " + max + " 条");
  dates.forEach(function (d) {
    var n = counts[d];
    var lf = ctx.getState().linkedFilter;
    var active = lf && lf.type === "date" && lf.date === d;
    var col = mount(box, "button", active ? "bar-col is-active" : "bar-col");
    col.type = "button";
    col.setAttribute("aria-label", d + "，命中 " + n + " 条；" + (active ? "取消当天筛选" : "仅查看当天报道"));
    col.setAttribute("aria-pressed", active ? "true" : "false");
    col.setAttribute("aria-controls", "rows");
    col.addEventListener("click", function () { toggleLinked(ctx, { type: "date", key: d, date: d, label: "日期 " + d }); });
    mount(col, "div", "bar-n", n);
    var bar = mount(col, "div", "bar");
    bar.setAttribute("aria-hidden", "true");
    bar.style.height = Math.max(3, Math.round((n / max) * 96)) + "px";
    bar.title = d + "：" + n + " 条";
    mount(col, "div", "bar-l", d.slice(5));
  });
}

function fallbackLayout(record) {
  var g = record.geometry;
  if (!g || typeof g.top !== "number" || typeof g.bottom !== "number" || g.top >= g.bottom) return null;
  return {
    basis: g.basis === "area" ? "area" : "mapping", aspect_ratio: 0.72, partial: true,
    items: [{
      url: record.url, top: g.top, bottom: g.bottom,
      left: typeof g.left === "number" ? g.left : 0.08,
      right: typeof g.right === "number" ? g.right : 0.92,
    }],
  };
}

export function renderLayouts(ctx, records) {
  var box = $("layouts"), empty = $("layouts-empty");
  if (!box || !empty) return;
  var groups = {};
  records.forEach(function (r) {
    var layout = r.layout || fallbackLayout(r);
    if (!layout || !Array.isArray(layout.items) || !layout.items.length) return;
    var key = [r.paper, r.date, r.section_no || "", r.section || "—"].join("\u0001");
    if (!groups[key]) {
      groups[key] = { key: key, paper: r.paper, date: r.date, section: r.section || "—",
        layout: layout, hitUrls: new Set(), titles: [] };
    }
    if (layout.items.length > groups[key].layout.items.length) groups[key].layout = layout;
    groups[key].hitUrls.add(r.url);
    if (r.title && groups[key].titles.indexOf(r.title) === -1) groups[key].titles.push(r.title);
  });
  clearNode(box);
  var list = Object.keys(groups).map(function (k) { return groups[k]; });
  list.sort(function (a, b) {
    if (a.date !== b.date) return a.date < b.date ? 1 : -1;
    return a.paper.localeCompare(b.paper, "zh") || a.section.localeCompare(b.section, "zh");
  });
  $("layouts-summary").textContent = list.length
    ? (list.length > MAX_LAYOUTS ? "显示 " + MAX_LAYOUTS + " / " + list.length + " 个版面" : list.length + " 个可绘制版面")
    : "暂无坐标";
  empty.hidden = list.length > 0;
  if (!list.length) {
    empty.textContent = records.length
      ? "这些记录的网站没有提供二维版面坐标；仍会显示“版内第几条”，但不会伪造位置图。重新扫描后，支持坐标的报社会自动出现。"
      : "当前筛选没有命中记录。";
    return;
  }
  list.slice(0, MAX_LAYOUTS).forEach(function (group) {
    var lf = ctx.getState().linkedFilter;
    var active = lf && lf.type === "layout" && lf.key === group.key;
    var card = mount(box, "article", active ? "layout-item is-active" : "layout-item");
    var page = mount(card, "div", "layout-page");
    page.style.aspectRatio = String(Math.max(0.35, Math.min(1.5, Number(group.layout.aspect_ratio) || 0.72)));
    page.setAttribute("role", "img");
    page.setAttribute("aria-label", group.paper + " " + group.date + " " + group.section +
      "版面位置图，命中 " + group.hitUrls.size + " 条，共 " + group.layout.items.length + " 个位置");
    group.layout.items.forEach(function (item) {
      var block = mount(page, "span", group.hitUrls.has(item.url) ? "layout-block hit" : "layout-block");
      block.style.left = pct(item.left);
      block.style.top = pct(item.top);
      block.style.width = pct(Math.max(0.004, item.right - item.left));
      block.style.height = pct(Math.max(0.004, item.bottom - item.top));
      block.title = group.hitUrls.has(item.url) ? "命中报道" : "本版其他报道";
    });
    var info = mount(card, "div", "layout-info");
    mount(info, "h3", null, group.paper + " · " + group.date);
    mount(info, "p", null, group.section);
    mount(info, "p", "layout-hit", "命中 " + group.hitUrls.size + " 条 / 本版 " + group.layout.items.length + " 个位置");
    group.titles.slice(0, 3).forEach(function (title) { mount(info, "p", null, "· " + title); });
    if (group.layout.partial) mount(info, "p", null, "历史记录仅保留命中块，背景文章将在下次扫描后补全。");
    var viewButton = mount(info, "button", active ? "btn subtle layout-action is-active" : "btn subtle layout-action",
      active ? "取消本版筛选" : "查看本版 " + group.hitUrls.size + " 篇报道");
    viewButton.type = "button";
    viewButton.setAttribute("aria-pressed", active ? "true" : "false");
    viewButton.setAttribute("aria-controls", "rows");
    viewButton.addEventListener("click", function () {
      toggleLinked(ctx, { type: "layout", key: group.key, urls: new Set(group.hitUrls),
        label: group.paper + " · " + group.date + " · " + group.section });
    });
    var legend = mount(info, "div", "layout-legend");
    legend.appendChild(legendItem("hit-key", "命中"));
    legend.appendChild(legendItem("", "本版其他报道"));
  });
}
function legendItem(className, text) {
  var span = el("span", className || null);
  span.appendChild(el("i"));
  span.appendChild(document.createTextNode(text));
  return span;
}

export function renderRows(state, records) {
  var body = $("rows");
  if (!body) return;
  clearNode(body);
  var fragment = document.createDocumentFragment();
  records.slice(0, state.visibleRows).forEach(function (r) {
    var tr = document.createElement("tr");
    var paperCell = mount(tr, "td", "col-paper", r.paper); paperCell.setAttribute("data-label", "报社");
    var dateCell = mount(tr, "td", "col-date", r.date); dateCell.setAttribute("data-label", "日期");
    var sectionText = r.section || "—";
    var position = geomText(r.geometry);
    if (position) sectionText += " · " + position;
    mount(tr, "td", "col-section", sectionText).setAttribute("data-label", "版面位置");
    var td = mount(tr, "td", "title");
    td.setAttribute("data-label", "报道");
    var url = safeUrl(r.url);
    if (url) {
      var a = mount(td, "a", null, r.title || r.url);
      a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer";
    } else mount(td, "span", null, r.title);
    (r.snippets || []).slice(0, 2).forEach(function (s) { mount(td, "div", "sn", s); });
    if (Array.isArray(r.matched) && r.matched.length) {
      var tags = mount(td, "div", "tags");
      r.matched.filter(function (k) { return state.keywords.has(k); })
        .forEach(function (k) { mount(tags, "span", "tag", k); });
    }
    fragment.appendChild(tr);
  });
  body.appendChild(fragment);
}

function renderResultContext(state, list) {
  var box = $("result-context");
  if (!box) return;
  var hasFocus = Array.isArray(state.focusKeywords) && state.focusKeywords.length > 0;
  var hasLinked = Boolean(state.linkedFilter);
  box.hidden = !hasFocus && !hasLinked;
  setHidden($("result-show-history"), !hasFocus);
  setHidden($("result-clear-linked"), !hasLinked);
  if (!hasFocus && !hasLinked) return;
  var parts = [];
  if (hasFocus) parts.push("当前关键词：" + state.focusKeywords.join("、"));
  if (hasLinked) parts.push("图表筛选：" + state.linkedFilter.label);
  setText($("result-context-title"), parts.join(" · "));
  setText($("result-context-detail"), list.length
    ? "三处视图已联动，当前显示 " + list.length + " 条命中报道。"
    : (hasFocus ? "当前关键词暂无命中；历史归档仍然保留，可切换查看。" : "当前图表筛选没有匹配报道，可取消筛选返回。"));
}
function renderShown(state, all, list) {
  var visibleCount = Math.min(list.length, state.visibleRows);
  setText($("shown"), list.length > visibleCount
    ? "显示 " + visibleCount + " / " + list.length + " 条" + (list.length === all.length ? "" : "（已筛选）")
    : (list.length === all.length ? "共 " + all.length + " 条" : "显示 " + list.length + " / " + all.length + " 条（已筛选）"));
  setHidden($("results-footer"), list.length <= visibleCount);
  setText($("results-limit"), list.length > visibleCount
    ? "已显示前 " + visibleCount + " 条，还有 " + (list.length - visibleCount) + " 条" : "");
}
function renderEmpty(state, all, list) {
  var box = $("empty");
  if (!box) return;
  if (all.length === 0) {
    clearNode(box);
    box.hidden = false;
    mount(box, "div", "empty-title", "还没有命中记录");
    var where = mount(box, "p", null, "看板读取的数据目录：");
    mount(where, "code", null, state.data.data_dir);
    mount(box, "p", null, "在页面顶部填写关键词并点「开始扫描」，完成后本列表会自动刷新。");
    if (!state.data.has_web_config) {
      var kws = (state.data.default_keywords || []).join("、") || "（默认关键词）";
      mount(box, "p", "empty-hint",
        "这是全新环境（尚未生成 state/web_config.json）：默认关键词为「" + kws + "」，默认扫描「今天 + 昨天」。");
    }
    return;
  }
  box.hidden = list.length > 0;
  box.textContent = state.focusKeywords
    ? "当前关键词「" + state.focusKeywords.join("、") + "」暂无命中；历史归档仍保留。"
    : "没有符合条件的命中。";
}
function renderRangeHint(state) {
  var hint = $("range-hint");
  if (!hint) return;
  if (state.from && state.to && state.from > state.to) setText(hint, "⚠ 起始日期晚于结束日期，结果为空");
  else if (state.from || state.to) setText(hint, (state.from || "…") + " ~ " + (state.to || "…"));
  else setText(hint, "");
}
function renderFooter(state) {
  var foot = $("foot");
  if (!foot || !state.data) return;
  clearNode(foot);
  foot.appendChild(document.createTextNode("数据目录："));
  mount(foot, "code", null, state.data.data_dir);
  foot.appendChild(document.createTextNode(" · 归档："));
  mount(foot, "code", null, state.data.archive_file);
  foot.appendChild(document.createTextNode(" · 页面顶部可直接开始扫描，结果写入该数据目录。"));
}

/* ------------------------------------------------------- 图形化看板 */
function rankRows(box, items, opts) {
  clearNode(box);
  if (!items.length) return emptyHint(box, opts.empty);
  var max = items[0].count || 1;
  items.slice(0, MAX_RANK).forEach(function (item) {
    var node = mount(box, opts.tag, "rank-row" + (opts.tag === "button" ? " rank-row-button" : ""));
    if (opts.tag === "button") node.type = "button";
    node.setAttribute("aria-label", item.name + "：" + opts.unit + " " + item.count +
      (opts.tag === "button" ? "；点击后仅保留该关键词" : ""));
    if (opts.onClick) node.addEventListener("click", function () { opts.onClick(item); });
    mount(node, "span", "rank-name", item.name);
    mount(mount(node, "div", "rank-track"), "span", "rank-bar" + (opts.alt ? " rank-bar-alt" : "")).style.width =
      pct(item.count / max);
    mount(node, "span", "rank-count", item.count);
  });
}
export function renderPaperRanking(state) {
  var box = $("paper-rank");
  if (!box) return;
  rankRows(box, paperRanking(state.data), { tag: "div", empty: "暂无报社命中数据。", unit: "命中" });
}
export function renderKeywordRanking(ctx, records) {
  var box = $("keyword-rank");
  if (!box) return;
  rankRows(box, keywordRanking(records), {
    tag: "button", alt: true, unit: "命中", empty: "当前筛选下没有命中关键词。",
    onClick: function (item) {
      ctx.dispatch({ type: A.SET_KEYWORDS, names: [item.name] });
      ctx.dispatch({ type: A.CLEAR_LINKED });
    },
  });
}
export function renderHeatmap(ctx, records) {
  var box = $("heatmap");
  if (!box) return;
  clearNode(box);
  var h = heatmap(records, MAX_HEATMAP_PAPERS, MAX_HEATMAP_DATES);
  if (!h.papers.length || !h.dates.length) return emptyHint(box, "当前筛选下暂无可绘制的日期 × 报社分布。");
  var grid = mount(box, "div", "heatmap-grid");
  grid.setAttribute("role", "group");
  grid.setAttribute("aria-label", "日期与报社命中热力图，点击单元格可按该报社与日期筛选");
  grid.style.gridTemplateColumns = "minmax(82px, 1.4fr) repeat(" + h.dates.length + ", minmax(20px, 1fr))";
  mount(grid, "span", "heatmap-corner", "报社 \\ 日期");
  h.dates.forEach(function (d) { mount(grid, "span", "heatmap-date", d.slice(5)).title = d; });
  h.papers.forEach(function (paper) {
    mount(grid, "span", "heatmap-paper", paper).title = paper;
    h.dates.forEach(function (d) {
      var count = h.cells[paper + HEATMAP_KEY_SEP + d] || 0;
      var level = count === 0 ? 0 : Math.max(1, Math.round((count / h.max) * 4));
      var cell = mount(grid, "button", "heatmap-cell level-" + level, count ? String(count) : "");
      cell.type = "button";
      cell.setAttribute("aria-label", paper + " " + d + "：" + count + " 条命中；点击按该报社与日期筛选");
      if (count === 0) cell.disabled = true;
      else cell.addEventListener("click", function () {
        toggleLinked(ctx, { type: "cell", key: paper + HEATMAP_KEY_SEP + d, paper: paper, date: d,
          label: "报社 " + paper + " · 日期 " + d });
      });
    });
  });
}
function renderExportPanel(state) {
  var scope = $("export-scope");
  if (!scope) return;
  var rows = state.data ? selectFiltered(state) : [];
  var title = scope.querySelector("b"), detail = scope.querySelector("span");
  if (title) setText(title, rows.length ? "将导出当前筛选的 " + rows.length + " 条报道" : "当前筛选没有可导出的报道");
  if (detail) setText(detail, rows.length
    ? "服务端会重新应用相同筛选，并返回实际保存路径与条数。"
    : "请关闭窗口并调整筛选条件后再试。");
  var busy = state.exportDialog.busy;
  var disabled = busy || rows.length === 0;
  $("export-save").disabled = disabled;
  $("export-browser").disabled = disabled;
  setText($("export-save"), busy ? "正在保存…" : "保存到文件夹");
  $("export-format").disabled = busy;
  $("export-directory").disabled = busy;
  var status = $("export-status");
  if (status) {
    setText(status, state.exportDialog.status);
    if (state.exportDialog.tone) status.setAttribute("data-state", state.exportDialog.tone);
    else status.removeAttribute("data-state");
  }
}
