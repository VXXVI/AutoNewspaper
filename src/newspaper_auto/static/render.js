/* render.js —— 所有「state → DOM」的渲染都在这里；事件只 dispatch，绝不先改 DOM 再发 action。
 * 零 innerHTML：一律 el()/mount()/textContent 建节点；chip/柱/热力格的 on 与 aria-pressed 由 state 推导。
 */
import {
  A, selectActiveFilters, selectFiltered, selectSorted, selectScanNames, countNewHits, newUrlSet,
  paperRanking, keywordRanking, heatmap, HEATMAP_KEY_SEP,
} from "./store.js";
import {
  $, el, mount, safeUrl, setText, setInputValue, clearNode, setHidden, chip,
} from "./dom.js";
import { geomText, todayIso } from "./format.js";

export var MAX_RANK = 8;
export var MAX_HEATMAP_PAPERS = 8;
export var MAX_HEATMAP_DATES = 14;
export var MAX_HISTORY = 8;

var SVG_NS = "http://www.w3.org/2000/svg"; // 建行内版面缩略图 / 指标卡图标都用它
var CHART_MAX_BAR = 150; // 柱高上限，与 style.css 里 .chart 的 min-height 同源

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
  renderFilterInputs(state);
  renderDateBounds(state);
  renderFilterPopover(state);
  renderControls(ctx, state);
  renderScanPanel(state);
  renderScanHistory(state);
  renderPapersPanel(ctx, state);
  if (state.data) {
    var all = state.data.records;
    var list = selectSorted(state);
    renderStats(state, all, list);
    renderChart(ctx, list);
    renderRows(ctx, state, list);
    renderResultContext(state, list);
    renderActiveFilters(ctx, state);
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
  renderServiceTip(state);
}

/* 服务不可用时给一条自助恢复路径：解释原因 + 一键重连，而不是只留一句「请确认看板仍在运行」。 */
var SERVICE_TIP =
  "看板服务已停止或已断开（关掉命令行窗口会停止前台看板）。请重新启动看板" +
  "（Windows 双击 NewspaperAuto.exe；macOS 双击 dashboard.command；" +
  "源码运行用 scripts/dashboard.cmd），然后点右侧按钮重连。";
function renderServiceTip(state) {
  var tip = $("service-tip"), text = $("service-tip-text");
  if (!tip) return;
  var down = state.system.ok === false;
  tip.hidden = !down;
  if (down && text) setText(text, SERVICE_TIP);
}
function renderFilterInputs(state) {
  setInputValue($("q"), state.q);
  setInputValue($("from"), state.from);
  setInputValue($("to"), state.to);
  var sort = $("sort");
  if (sort && sort.value !== state.sort) sort.value = state.sort;
}

/* 筛选面板做成结果卡右上角的浮层：收起时零占位，整行宽度都留给命中列表。
 * 展开状态记忆在 localStorage（app.js），刷新/重开看板后保持。 */
function renderFilterPopover(state) {
  var open = state.filtersOpen === true;
  setHidden($("filter-panel"), !open);
  var btn = $("filter-toggle");
  if (btn) btn.setAttribute("aria-expanded", open ? "true" : "false");
  var count = activeFilterCount(state);
  setHidden($("filter-count"), count === 0);
  setText($("filter-count"), String(count));
  if (btn) {
    btn.title = count
      ? "已启用 " + count + " 项筛选，点此查看或调整"
      : "按时间、报社、关键词缩小范围";
  }
}

/* 当前启用的筛选条件数：既是「筛选」按钮上的角标，也让收起状态下的筛选强度可见。
 * 普通全选不计数；配置的关键词聚焦仍计数，以便用户取消并查看历史。 */
export function activeFilterCount(state) {
  return selectActiveFilters(state).length;
}

export function renderActiveFilters(ctx, state) {
  var box = $("active-filters");
  if (!box) return;
  var filters = selectActiveFilters(state);
  clearNode(box);
  box.hidden = !filters.length;
  filters.forEach(function (filter) {
    var button = mount(box, "button", "active-filter-tag", filter.label + " ×");
    button.type = "button";
    button.title = "取消" + filter.label;
    button.setAttribute("aria-label", "取消" + filter.label);
    button.setAttribute("data-filter-key", filter.key);
    button.addEventListener("click", function () {
      ctx.dispatch({ type: A.CLEAR_FILTER, key: filter.key }, { immediate: true });
      var next = box.querySelector("button");
      if (next) next.focus(); else if ($("filter-toggle")) $("filter-toggle").focus();
    });
  });
}

function renderDateBounds(state) {
  // 上限优先用服务端下发的「今天」（应用时区），避免 VPS 为 UTC 而浏览器为北京时间时
  // 两边各算各的；数据尚未加载时回落到浏览器本地日期。
  var today = (state.data && state.data.today) || todayIso();
  var max = today;
  var min = "";
  if (state.data) {
    var dates = Object.keys(state.data.date_counts).sort();
    if (dates.length) {
      min = dates[0];
      var latestDataDate = dates[dates.length - 1];
      // 数据最新日期与今天取较小者，但永远不能超过今天
      max = latestDataDate > today ? today : latestDataDate;
    }
  }
  ["from", "to"].forEach(function (id) {
    var input = $(id);
    if (input) {
      if (min) input.min = min;
      input.max = max;
    }
  });
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
  renderNewOnlyChip(state);
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

/* 「只看本次新增」chip：数据来自 state/last_new.json（后端下发的 new_urls）。
 * 没有快照时禁用并说明原因，别让用户点进一个恒为空的筛选。 */
function renderNewOnlyChip(state) {
  var btn = $("new-only");
  if (!btn) return;
  var count = countNewHits(state);
  var available = Boolean(state.data && (state.data.new_urls || []).length);
  btn.disabled = !available;
  btn.classList.toggle("on", state.newOnly === true);
  btn.setAttribute("aria-pressed", state.newOnly ? "true" : "false");
  setText(btn, count ? "只看本次新增（" + count + "）" : "只看本次新增");
  var hint = $("new-only-hint");
  if (!hint) return;
  if (!available) setText(hint, "还没有新增记录：完成一次扫描后，这里会列出本次新增的报道。");
  else if (state.data.last_new_at) setText(hint, "最近一次扫描 " + state.data.last_new_at + " 新增 " + count + " 条。");
  else setText(hint, "本次新增 " + count + " 条。");
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
  var recent = $("scan-recent");
  if (recent) {
    recent.hidden = scan.running;
    var history = scan.history || [];
    var latest = history[history.length - 1];
    var summary = latest && latest.summary;
    setText(recent, latest ? "最近扫描 · " + (latest.finished_at || latest.started_at || "时间未记录") +
      " · " + (HISTORY_STATUS[latest.status] || ["未知"])[0] +
      (summary && typeof summary.hits === "number" ? " · 命中 " + summary.hits + " 篇" : "") +
      (summary && typeof summary.new_hits === "number" ? " · 新增 " + summary.new_hits + " 篇" : "")
      : (scan.history === null ? "正在读取最近扫描…" : "开始首次扫描，命中报道会自动出现在下方。"));
  }
  renderScanProgress(!scan.running && !(scan.progress && scan.progress.total) ? null : scan.progress);
  var log = $("scan-log");
  if (log) {
    // 智能吸底：用户往上翻看历史日志时不要把他拽回底部；只有本来就在底部才跟随。
    // 必须在写入新内容**之前**判断，否则 scrollHeight 已经变了。
    var atBottom = log.scrollHeight - log.scrollTop - log.clientHeight <= 24;
    setText(log, scan.logLines.join("\n"));
    if (atBottom) log.scrollTop = log.scrollHeight;
  }
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
        if (item.source === "override") {
          // 覆盖层可以单独摘掉：摘掉后回到内置默认规则（内置本身还在）
          paperButton(actions, "恢复默认", "恢复" + name + "的内置默认规则", function () {
            ctx.features.papers.deletePaper(name, item.source);
          });
        }
        // 所有报社（含内置六家）都能**彻底删除**：删后从本列表消失、没有恢复入口。
        paperButton(actions, "删除", "彻底删除" + name, function () {
          ctx.features.papers.removePaper(name);
        }, "chip danger");
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
/* 指标卡的图标：语义化线稿，替代原先无语义的纯色方块。
 * 路径写成 SVG path 的 d 属性，统一 24×24 视口、描边样式交给 CSS。 */
var STAT_ICONS = {
  archive: "M3 7h18v3H3zM5 10v9h14v-9M9 14h6",
  filter: "M4 5h16l-6 7v6l-4 2v-8z",
  papers: "M4 5h11v14H4zM15 8h5v11h-5M7 9h5M7 12h5M7 15h3",
  calendar: "M4 6h16v14H4zM4 10h16M8 3v4M16 3v4",
  history: "M12 7v5l3 2M3.5 12a8.5 8.5 0 1 0 2.6-6.1M3 4v4h4",
};
var STAT_TONES = ["brand", "teal", "amber", "violet", "slate"];

function statIcon(name) {
  var box = el("i", "stat-icon");
  box.setAttribute("aria-hidden", "true");
  var svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  var path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", STAT_ICONS[name] || STAT_ICONS.archive);
  svg.appendChild(path);
  box.appendChild(svg);
  return box;
}

export function renderStats(state, all, list) {
  var data = state.data;
  var seenTotal = Object.keys(data.seen_counts).reduce(function (n, k) { return n + data.seen_counts[k]; }, 0);
  var dates = Array.from(new Set(list.map(function (r) { return r.date; }))).sort();
  var papers = Array.from(new Set(list.map(function (r) { return r.paper; })));
  var cards = [
    { k: "全部归档", v: all.length, x: "历史命中报道", icon: "archive" },
    {
      k: "当前结果", v: list.length, icon: "filter",
      x: activeFilterCount(state) ? "已启用 " + activeFilterCount(state) + " 项筛选 · 共 " + all.length + " 条归档" : "未使用筛选",
    },
    { k: "相关报社", v: papers.length, icon: "papers", x: papers.join("、") || "暂无报社" },
    {
      k: "覆盖日期", v: dates.length, icon: "calendar",
      x: dates.length ? dates[0] + " 至 " + dates[dates.length - 1] : "暂无日期",
    },
    { k: "历史已检索", v: seenTotal, icon: "history", x: "所有关键词累计，用于识别新增" },
  ];
  var box = $("stats");
  if (!box) return;
  clearNode(box);
  cards.forEach(function (item, index) {
    var card = mount(box, "article", "stat");
    card.setAttribute("data-tone", STAT_TONES[index] || "brand");
    card.setAttribute("aria-label", item.k + "：" + item.v + (item.x ? "，" + item.x : ""));
    card.appendChild(statIcon(item.icon));
    mount(card, "div", "k", item.k);
    mount(card, "div", "v", item.v);
    if (item.x) { var detail = mount(card, "div", "x", item.x); detail.title = item.x; }
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
    bar.style.height = Math.max(3, Math.round((n / max) * CHART_MAX_BAR)) + "px";
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

/* 版面分组的键：必须与后端 web_dashboard.layout_key 完全一致（\u0001 分隔）。
 * 后端把同一版面的 layout 抽到顶层 `layouts` 去重，这里按同键取回共享的那一份。 */
var LAYOUT_KEY_SEP = "\u0001";
export function layoutKeyOf(r) {
  return [r.paper, r.date, r.section_no || "", r.section || "—"].join(LAYOUT_KEY_SEP);
}

/* ------------------------------------------------------- 行内版面位置缩略图
 *
 * 目标：**一条命中对应一个位置图**，紧贴在标题右侧，扫读时不必往页面下方找整节版面图。
 *
 * 性能：一次默认渲染 50 行（「显示更多」还在加），而一个版面动辄几十个块。
 * 若每块一个 DOM 节点，光缩略图就是几千个节点；这里把**非命中块预先合成一条 SVG
 * path**（按 layout 对象缓存、整版共享），每行只多一个命中 <rect>。
 */
var MAP_UNIT = 1000; // 缩略图 viewBox 边长：版面坐标是 0~1 的归一化值
var _mapCache = new WeakMap();

function mapNumber(value, fallback) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}
function mapUnit(value) {
  return Math.round(value * MAP_UNIT * 10) / 10;
}

/** 取这条记录所属版面的布局：后端的共享 layouts → 老接口逐条 layout → geometry 近似图。 */
function layoutOf(state, record) {
  var layoutMap = (state.data && state.data.layouts) || {};
  return layoutMap[layoutKeyOf(record)] || record.layout || fallbackLayout(record);
}

/** 把整版的非命中块压成一条 path。同一份 layout 只算一次（WeakMap 缓存）。 */
function mapGeometry(layout) {
  var cached = _mapCache.get(layout);
  if (cached) return cached;
  var path = "";
  (layout.items || []).forEach(function (item) {
    var left = mapNumber(item.left, 0), top = mapNumber(item.top, 0);
    var width = mapNumber(item.right, 1) - left, height = mapNumber(item.bottom, 1) - top;
    if (width <= 0 || height <= 0) return;
    // 相对段拼矩形（M/h/v/h/z）比重复写绝对坐标短得多，几十个块也只有几百字节
    path += "M" + mapUnit(left) + " " + mapUnit(top) +
      "h" + mapUnit(width) + "v" + mapUnit(height) + "h" + mapUnit(-width) + "z";
  });
  cached = {
    path: path,
    aspect: Math.max(0.35, Math.min(1.5, Number(layout.aspect_ratio) || 0.72)),
  };
  _mapCache.set(layout, cached);
  return cached;
}

/**
 * 生成一条命中的版面位置缩略图（`<button>`，点一下 = 只看本版报道，等同旧的
 * 「查看本版 N 篇」）。没有坐标数据时返回 null，由调用方只渲染文字。
 */
function rowMap(ctx, state, record, groupUrls) {
  var layout = layoutOf(state, record);
  if (!layout || !Array.isArray(layout.items) || !layout.items.length) return null;
  var geometry = mapGeometry(layout);
  var key = layoutKeyOf(record);
  var linked = state.linkedFilter;
  var active = Boolean(linked && linked.type === "layout" && linked.key === key);
  var action = active ? "取消本版筛选" : "只看本版 " + groupUrls.size + " 篇报道";

  var box = el("button", active ? "row-map is-active" : "row-map");
  box.type = "button";
  // 版面宽高比靠外层 aspect-ratio 决定，viewBox 用 preserveAspectRatio="none"
  // 让 x/y 各自归一化 —— 这正是 items 里 left/right 与 top/bottom 的原始口径。
  box.style.aspectRatio = String(geometry.aspect);
  box.title = action;
  box.setAttribute("aria-label", (record.section || "本版") + " 版面位置图；" + action);
  box.setAttribute("aria-pressed", active ? "true" : "false");
  box.setAttribute("aria-controls", "rows");
  box.addEventListener("click", function () {
    toggleLinked(ctx, { type: "layout", key: key, urls: groupUrls,
      label: record.paper + " · " + record.date + " · " + (record.section || "—") });
  });

  var svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 " + MAP_UNIT + " " + MAP_UNIT);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("aria-hidden", "true");
  var base = document.createElementNS(SVG_NS, "path");
  base.setAttribute("class", "map-base");
  base.setAttribute("d", geometry.path);
  svg.appendChild(base);

  var hit = layout.items.filter(function (item) { return item.url === record.url; })[0];
  if (hit) {
    var left = mapNumber(hit.left, 0), top = mapNumber(hit.top, 0);
    var rect = document.createElementNS(SVG_NS, "rect");
    rect.setAttribute("class", "map-hit");
    rect.setAttribute("x", String(mapUnit(left)));
    rect.setAttribute("y", String(mapUnit(top)));
    rect.setAttribute("width", String(Math.max(2, mapUnit(mapNumber(hit.right, 1) - left))));
    rect.setAttribute("height", String(Math.max(2, mapUnit(mapNumber(hit.bottom, 1) - top))));
    svg.appendChild(rect);
  }
  box.appendChild(svg);
  return box;
}

/** 归档只保留命中片段，预览不会请求或伪造完整正文。原文始终保留独立入口。 */
export function openArticlePreview(ctx, state, record, trigger) {
  var existing = $("article-preview");
  if (existing) existing.remove();
  var dialog = el("dialog", "article-preview");
  dialog.id = "article-preview";
  dialog.setAttribute("aria-labelledby", "article-preview-title");
  var head = mount(dialog, "div", "article-preview-head");
  mount(head, "span", "eyebrow", "报道预览");
  var close = mount(head, "button", "btn secondary article-preview-close", "关闭");
  close.type = "button";
  close.setAttribute("aria-label", "关闭报道预览");
  var title = mount(dialog, "h2", null, record.title || "未命名报道");
  title.id = "article-preview-title";
  mount(dialog, "p", "article-preview-meta", [record.paper, record.date, record.section].filter(Boolean).join(" · "));
  var body = mount(dialog, "div", "article-preview-body");
  if ((record.matched || []).length) {
    var tags = mount(body, "div", "tags");
    record.matched.forEach(function (keyword) { mount(tags, "span", "tag", keyword); });
  }
  mount(body, "h3", null, "命中片段");
  if ((record.snippets || []).length) {
    record.snippets.forEach(function (snippet) { mount(body, "p", "article-preview-snippet", snippet); });
  } else mount(body, "p", "empty-hint", "这条归档没有保存命中片段，可打开原文阅读。");
  mount(body, "p", "hint", "预览内容为归档中的命中片段，完整报道请查看原文。");
  var position = geomText(record.geometry);
  var map = rowMap(ctx, state, record, new Set([record.url]));
  if (map || position) {
    var location = mount(body, "div", "article-preview-location");
    mount(location, "h3", null, "版面位置");
    if (position) mount(location, "p", null, position);
    if (map) {
      // 预览里的缩略图只展示位置，避免在阅读时意外改变列表筛选。
      var figure = mount(location, "div", "row-map preview-map");
      figure.style.aspectRatio = map.style.aspectRatio;
      figure.setAttribute("role", "img");
      figure.setAttribute("aria-label", (record.section || "本版") + "，高亮区域为此报道的位置");
      figure.appendChild(map.querySelector("svg"));
    }
  }
  var actions = mount(dialog, "div", "article-preview-actions");
  var url = safeUrl(record.url);
  if (url) {
    var original = mount(actions, "a", "btn primary", "打开原文 ↗");
    original.href = url; original.target = "_blank"; original.rel = "noopener noreferrer";
  }
  function restoreFocus() {
    if (trigger && trigger.isConnected) trigger.focus();
    else {
      var replacement = Array.from(document.querySelectorAll(".article-preview-trigger"))
        .find(function (button) { return button.getAttribute("aria-label") === "预览报道：" + (record.title || record.url); });
      if (replacement) replacement.focus(); else if ($("filter-toggle")) $("filter-toggle").focus();
    }
  }
  function dismiss() {
    if (typeof dialog.close === "function") dialog.close();
    dialog.remove();
    restoreFocus();
  }
  close.addEventListener("click", dismiss);
  dialog.addEventListener("cancel", function (event) { event.preventDefault(); dismiss(); });
  dialog.addEventListener("click", function (event) {
    if (event.target !== dialog) return;
    var rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dismiss();
  });
  dialog.addEventListener("keydown", function (event) {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); dismiss(); }
    else if (event.key === "Tab") {
      var focusable = Array.from(dialog.querySelectorAll("button, a[href]"));
      var first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  document.body.appendChild(dialog);
  if (typeof dialog.showModal === "function") dialog.showModal();
  else { dialog.setAttribute("open", ""); dialog.setAttribute("aria-modal", "true"); }
  close.focus();
}

export function renderRows(ctx, state, records) {
  var body = $("rows");
  if (!body) return;
  clearNode(body);
  // 先按版面聚一遍：同一版的命中集合既用于缩略图的「只看本版 N 篇」，也是点击后联动筛选的依据。
  var groupUrls = {};
  records.forEach(function (r) {
    var key = layoutKeyOf(r);
    if (!groupUrls[key]) groupUrls[key] = new Set();
    groupUrls[key].add(r.url);
  });
  var fragment = document.createDocumentFragment();
  var newUrls = newUrlSet(state.data);
  records.slice(0, state.visibleRows).forEach(function (r) {
    var isNew = newUrls.has(r.url);
    var tr = document.createElement("tr");
    if (isNew) {
      tr.className = "is-new";
      tr.setAttribute("data-new", "true");
    }
    var paperCell = mount(tr, "td", "col-paper", r.paper); paperCell.setAttribute("data-label", "报社");
    var dateCell = mount(tr, "td", "col-date", r.date); dateCell.setAttribute("data-label", "日期");
    var td = mount(tr, "td", "title");
    td.setAttribute("data-label", "报道");
    var url = safeUrl(r.url);
    if (url) {
      var a = el("a");
      a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer";
      if (isNew) a.appendChild(el("span", "tag-new", "新增"));
      a.appendChild(document.createTextNode(r.title || r.url));
      td.appendChild(a);
    } else {
      if (isNew) td.appendChild(el("span", "tag-new", "新增"));
      mount(td, "span", null, r.title);
    }
    (r.snippets || []).slice(0, 2).forEach(function (s) { mount(td, "div", "sn", s); });
    if (Array.isArray(r.matched) && r.matched.length) {
      var tags = mount(td, "div", "tags");
      r.matched.filter(function (k) { return state.keywords.has(k); })
        .forEach(function (k) { mount(tags, "span", "tag", k); });
    }
    var preview = mount(td, "button", "btn secondary small article-preview-trigger", "预览");
    preview.type = "button";
    preview.setAttribute("aria-label", "预览报道：" + (r.title || r.url));
    preview.setAttribute("aria-haspopup", "dialog");
    preview.addEventListener("click", function () { openArticlePreview(ctx, state, r, preview); });
    // 版面位置：一条报道对应一格缩略位置图，紧跟在命中报道右侧。
    var posCell = mount(tr, "td", "col-section");
    posCell.setAttribute("data-label", "版面位置");
    var cellBody = mount(posCell, "div", "cell-body");
    var map = rowMap(ctx, state, r, groupUrls[layoutKeyOf(r)]);
    if (map) cellBody.appendChild(map);
    var text = mount(cellBody, "div", "cell-text");
    mount(text, "span", "section-name", r.section || "—");
    var position = geomText(r.geometry);
    if (position) mount(text, "span", "section-pos", position);
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
  if (state.newOnly && !(state.data.new_urls || []).length) {
    box.textContent = "还没有新增记录；完成一次扫描后即可按「只看本次新增」筛选。";
  } else if (state.newOnly) {
    box.textContent = "本次新增在当前其它筛选条件下没有匹配报道，可放宽筛选或关闭「只看本次新增」。";
  } else if (state.focusKeywords) {
    box.textContent = "当前关键词「" + state.focusKeywords.join("、") + "」暂无命中；历史归档仍保留。";
  } else {
    box.textContent = "没有符合条件的命中。";
  }
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
  // 固定上限而不是 1fr：否则宽屏上 14 列会被拉成每列 80px，格子之间裂开大片空白
  grid.style.gridTemplateColumns = "minmax(72px, 128px) repeat(" + h.dates.length + ", minmax(24px, 42px))";
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
  // 系统窗口是看板那台机器弹的：它弹不出来时（VPS / 无图形界面）就把按钮收起来，
  // 只留手动填写 —— 否则用户会反复点一个永远不会响应的按钮。
  var picker = state.exportDialog.picker;
  var browse = $("export-browse");
  if (browse) {
    browse.hidden = picker === "unsupported";
    browse.disabled = busy || picker === "busy";
    setText(browse, picker === "busy" ? "等待选择…" : "浏览…");
  }
  var status = $("export-status");
  if (status) {
    setText(status, state.exportDialog.status);
    if (state.exportDialog.tone) status.setAttribute("data-state", state.exportDialog.tone);
    else status.removeAttribute("data-state");
  }
}
