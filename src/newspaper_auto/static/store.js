/* 前端状态：严格 action + 纯 reducer（单向数据流）。
 *
 * 唯一变更入口是 store.dispatch(action)：reducer(state, action) 计算**新的** state，
 * 再通知订阅者重渲染。reducer 是纯函数（不读写外部变量，Set 一律产生新实例），
 * 因此可以脱离 DOM 单独测试。零框架、零构建、零第三方依赖；供 app.js 使用。
 *
 * 除 reducer 外，这里还放**纯选择器**（selectFiltered/selectSorted/...）：把
 * 「state → 视图数据」的推导集中在一处，render 只负责把数据画成 DOM。
 */

import { splitKeywords } from "./format.js";

export var INITIAL_ROWS = 50;
export var ROW_STEP = 50;
export var MAX_SCAN_LOG_LINES = 300;

// action 类型常量：避免散落的字符串字面量拼错。
export var A = {
  LOAD_DATA: "LOAD_DATA",
  SET_QUERY: "SET_QUERY",
  SET_DATE_RANGE: "SET_DATE_RANGE",
  SET_SORT: "SET_SORT",
  SET_PRESET: "SET_PRESET",
  SET_LINKED: "SET_LINKED",
  CLEAR_LINKED: "CLEAR_LINKED",
  SET_NEW_ONLY: "SET_NEW_ONLY",
  TOGGLE_PAPER: "TOGGLE_PAPER",
  TOGGLE_KEYWORD: "TOGGLE_KEYWORD",
  SET_KEYWORDS: "SET_KEYWORDS",
  SHOW_ALL_HISTORY: "SHOW_ALL_HISTORY",
  RESET_VIEW: "RESET_VIEW",
  CLEAR_FILTER: "CLEAR_FILTER",
  LOAD_MORE: "LOAD_MORE",
  SET_FILTERS_OPEN: "SET_FILTERS_OPEN",
  // 扫描运行状态（含进度、日志、本次可选报社）
  SCAN_SET_PAPERS: "SCAN_SET_PAPERS",
  SCAN_CONFIG_ONLY: "SCAN_CONFIG_ONLY",
  SCAN_TOGGLE_PAPER: "SCAN_TOGGLE_PAPER",
  SCAN_RUNNING: "SCAN_RUNNING",
  SCAN_CANCELLING: "SCAN_CANCELLING",
  SCAN_STATUS: "SCAN_STATUS",
  SCAN_PROGRESS: "SCAN_PROGRESS",
  SCAN_RESET_LOG: "SCAN_RESET_LOG",
  SCAN_INGEST: "SCAN_INGEST",
  SCAN_HISTORY: "SCAN_HISTORY",
  // 报社管理
  PAPERS_VIEW: "PAPERS_VIEW",
  PAPERS_STATUS: "PAPERS_STATUS",
  PAPERS_HINT: "PAPERS_HINT",
  PAPERS_EDITING: "PAPERS_EDITING",
  // 导出对话框
  EXPORT_BUSY: "EXPORT_BUSY",
  EXPORT_STATUS: "EXPORT_STATUS",
  // 原生文件夹选择器状态：idle / busy / unsupported（这台设备弹不出窗口）
  EXPORT_PICKER: "EXPORT_PICKER",
  // 头部服务状态 / 数据新鲜度
  SYSTEM_LOADING: "SYSTEM_LOADING",
  SYSTEM_ERROR: "SYSTEM_ERROR",
  SYSTEM_HEALTH: "SYSTEM_HEALTH"
};

export function createInitialState() {
  return {
    data: null,
    q: "",
    papers: new Set(),
    keywords: new Set(),
    focusKeywords: null,
    linkedFilter: null,
    from: "",
    to: "",
    sort: "date-desc",
    preset: "all",
    // 只看「最近一次扫描的新增命中」（data.new_urls 提供）。
    newOnly: false,
    visibleRows: INITIAL_ROWS,
    // 结果区的筛选面板是否展开（收起来后结果表占满整行）。选择由 app.js 记忆在 localStorage。
    filtersOpen: false,
    // 扫描面板：运行标志、可选/已选报社、进度、日志、历史
    scan: {
      running: false,
      cancelling: false,
      paperNames: [],
      selected: null,
      pendingOnly: null,
      logLines: [],
      logCursor: 0,
      taskId: null,
      status: { text: "准备就绪", tone: "idle" },
      progress: null,
      history: null
    },
    // 报社管理面板（filter 用的 papers 是 Set，见上方）
    papersPanel: {
      view: null,
      status: "—",
      tone: "idle",
      hint: "",
      editing: null
    },
    // 导出对话框
    exportDialog: {
      busy: false,
      status: "",
      tone: "",
      // picker: "idle" 可点 / "busy" 等窗口 / "unsupported" 该设备弹不出窗口
      picker: "idle"
    },
    // 头部系统状态
    system: {
      loading: false,
      error: "",
      ok: null,
      version: "",
      checkedAt: ""
    }
  };
}

function withPatch(state, patch) {
  return Object.assign({}, state, patch);
}

function slicePatch(state, key, patch) {
  var next = {};
  next[key] = Object.assign({}, state[key], patch);
  return withPatch(state, next);
}

function toggledSet(source, name, on) {
  var next = new Set(source);
  if (on) next.add(name); else next.delete(name);
  return next;
}

// 把配置里的 only（逗号分隔）套用到可选报社上：无显式选择返回 null（全选），
// 有显式选择但交集为空返回空 Set（保留空集合，绝不静默扩大为全选）。
function applyOnly(names, onlyText) {
  var wanted = splitKeywords(onlyText || "");
  if (!wanted.length) return null;
  var selected = new Set();
  wanted.forEach(function (name) {
    if (names.indexOf(name) !== -1) selected.add(name);
  });
  return selected;
}

export function reducer(state, action) {
  switch (action.type) {
    case A.LOAD_DATA: {
      var data = action.data;
      var patch = { data: data };
      if (action.mode === "reset") {
        patch.papers = new Set(data.papers);
        patch.focusKeywords = action.requestedFocus ? action.requestedFocus.slice() : null;
        patch.keywords = new Set(action.requestedFocus || data.keywords);
        patch.linkedFilter = null;
        patch.q = "";
        patch.from = "";
        patch.to = "";
        patch.sort = "date-desc";
        patch.preset = "all";
        patch.newOnly = false;
        patch.visibleRows = INITIAL_ROWS;
      } else {
        patch.papers = action.allPapersSelected
          ? new Set(data.papers)
          : new Set(data.papers.filter(function (name) { return state.papers.has(name); }));
        patch.keywords = state.focusKeywords
          ? new Set(state.focusKeywords)
          : (action.allKeywordsSelected
            ? new Set(data.keywords)
            : new Set(data.keywords.filter(function (name) { return state.keywords.has(name); })));
      }
      return withPatch(state, patch);
    }
    case A.SET_QUERY:
      return withPatch(state, { q: action.value, linkedFilter: null, visibleRows: INITIAL_ROWS });
    case A.SET_DATE_RANGE: {
      var range = { preset: "custom", linkedFilter: null, visibleRows: INITIAL_ROWS };
      if (action.from !== undefined) range.from = action.from;
      if (action.to !== undefined) range.to = action.to;
      return withPatch(state, range);
    }
    case A.SET_SORT:
      return withPatch(state, { sort: action.value, visibleRows: INITIAL_ROWS });
    case A.SET_PRESET: {
      var preset = { linkedFilter: null, preset: action.preset, visibleRows: INITIAL_ROWS };
      if (action.from !== undefined) preset.from = action.from;
      if (action.to !== undefined) preset.to = action.to;
      return withPatch(state, preset);
    }
    case A.SET_LINKED:
      return withPatch(state, { linkedFilter: action.filter, visibleRows: INITIAL_ROWS });
    case A.CLEAR_LINKED:
      return withPatch(state, { linkedFilter: null, visibleRows: INITIAL_ROWS });
    case A.SET_NEW_ONLY:
      return withPatch(state, { newOnly: action.on === true, visibleRows: INITIAL_ROWS });
    case A.TOGGLE_PAPER:
      return withPatch(state, { papers: toggledSet(state.papers, action.name, action.on) });
    case A.TOGGLE_KEYWORD:
      return withPatch(state, {
        keywords: toggledSet(state.keywords, action.name, action.on),
        focusKeywords: null
      });
    case A.SET_KEYWORDS:
      return withPatch(state, {
        keywords: new Set(action.names || []),
        focusKeywords: null,
        linkedFilter: null,
        visibleRows: INITIAL_ROWS
      });
    case A.SHOW_ALL_HISTORY:
      if (!state.data) return state;
      return withPatch(state, {
        focusKeywords: null,
        keywords: new Set(state.data.keywords),
        linkedFilter: null,
        visibleRows: INITIAL_ROWS
      });
    case A.CLEAR_FILTER: {
      // 每个标签只清除自身条件，避免搜索/日期的旧 action 顺带清掉图表联动。
      var cleared = { visibleRows: INITIAL_ROWS };
      if (action.key === "query") cleared.q = "";
      else if (action.key === "dates") { cleared.from = ""; cleared.to = ""; cleared.preset = "all"; }
      else if (action.key === "papers" && state.data) cleared.papers = new Set(state.data.papers);
      else if (action.key === "keywords" && state.data) {
        cleared.keywords = new Set(state.data.keywords); cleared.focusKeywords = null;
      } else if (action.key === "linked") cleared.linkedFilter = null;
      else if (action.key === "newOnly") cleared.newOnly = false;
      else return state;
      return withPatch(state, cleared);
    }
    case A.RESET_VIEW: {
      var reset = {
        linkedFilter: null, q: "", from: "", to: "",
        sort: "date-desc", preset: "all", newOnly: false, visibleRows: INITIAL_ROWS
      };
      if (state.data) {
        reset.papers = new Set(state.data.papers);
        reset.keywords = new Set(state.focusKeywords || state.data.keywords);
      }
      return withPatch(state, reset);
    }
    case A.LOAD_MORE:
      return withPatch(state, { visibleRows: state.visibleRows + ROW_STEP });
    case A.SET_FILTERS_OPEN:
      // 筛选是结果卡右上角的浮层，缺省收起：只有显式 open:true 才展开。
      return withPatch(state, { filtersOpen: action.open === true });

    /* ---------------------------------------------------------- 扫描状态 */
    case A.SCAN_SET_PAPERS: {
      var names = (action.names || []).slice();
      var pending = state.scan.pendingOnly;
      var selected;
      if (pending !== null) {
        selected = applyOnly(names, pending);
        pending = null;
      } else if (state.scan.selected) {
        selected = new Set(names.filter(function (name) { return state.scan.selected.has(name); }));
      } else {
        selected = null;
      }
      return slicePatch(state, "scan", { paperNames: names, selected: selected, pendingOnly: pending });
    }
    case A.SCAN_CONFIG_ONLY: {
      var only = typeof action.only === "string" ? action.only : "";
      if (state.scan.paperNames.length) {
        return slicePatch(state, "scan", {
          selected: applyOnly(state.scan.paperNames, only),
          pendingOnly: null
        });
      }
      return slicePatch(state, "scan", { pendingOnly: only });
    }
    case A.SCAN_TOGGLE_PAPER: {
      var base = state.scan.selected ? new Set(state.scan.selected) : new Set(state.scan.paperNames);
      if (action.on) base.add(action.name); else base.delete(action.name);
      return slicePatch(state, "scan", { selected: base, pendingOnly: null });
    }
    case A.SCAN_RUNNING:
      return slicePatch(state, "scan", { running: Boolean(action.running), cancelling: false });
    case A.SCAN_CANCELLING:
      return slicePatch(state, "scan", { cancelling: Boolean(action.cancelling) });
    case A.SCAN_STATUS:
      return slicePatch(state, "scan", {
        status: { text: String(action.text || ""), tone: action.tone || "idle" }
      });
    case A.SCAN_PROGRESS:
      return slicePatch(state, "scan", { progress: action.progress || null });
    case A.SCAN_RESET_LOG:
      return slicePatch(state, "scan", { logLines: [], logCursor: 0, progress: null });
    case A.SCAN_INGEST: {
      var scan = state.scan;
      var task = action.task || {};
      var replace = action.replace === true;
      var taskId = scan.taskId;
      var cursor = scan.logCursor;
      if (task.task_id && taskId && task.task_id !== taskId) { replace = true; cursor = 0; }
      if (task.task_id) taskId = task.task_id;
      var lines = replace || task.logs_truncated ? [] : scan.logLines;
      if (Array.isArray(task.log) && task.log.length) {
        lines = lines.concat(task.log).slice(-MAX_SCAN_LOG_LINES);
      }
      if (typeof task.log_cursor === "number") cursor = task.log_cursor;
      return slicePatch(state, "scan", {
        taskId: taskId,
        logLines: lines,
        logCursor: cursor,
        progress: task.progress === undefined ? scan.progress : task.progress
      });
    }
    case A.SCAN_HISTORY:
      return slicePatch(state, "scan", {
        history: Array.isArray(action.tasks) ? action.tasks.slice() : []
      });

    /* ---------------------------------------------------------- 报社管理 */
    case A.PAPERS_VIEW:
      return slicePatch(state, "papersPanel", { view: action.view || null });
    case A.PAPERS_STATUS:
      return slicePatch(state, "papersPanel", {
        status: String(action.text === undefined ? "" : action.text),
        tone: action.tone || "idle"
      });
    case A.PAPERS_HINT:
      return slicePatch(state, "papersPanel", { hint: String(action.text || "") });
    case A.PAPERS_EDITING:
      return slicePatch(state, "papersPanel", { editing: action.name || null });

    /* ---------------------------------------------------------- 导出对话框 */
    case A.EXPORT_BUSY:
      return slicePatch(state, "exportDialog", { busy: Boolean(action.busy) });
    case A.EXPORT_STATUS:
      return slicePatch(state, "exportDialog", {
        status: String(action.text || ""),
        tone: action.tone || ""
      });
    case A.EXPORT_PICKER:
      return slicePatch(state, "exportDialog", { picker: String(action.value || "idle") });

    /* ---------------------------------------------------------- 系统状态 */
    case A.SYSTEM_LOADING:
      return slicePatch(state, "system", { loading: Boolean(action.loading) });
    case A.SYSTEM_ERROR:
      return slicePatch(state, "system", { error: String(action.message || "") });
    case A.SYSTEM_HEALTH:
      return slicePatch(state, "system", {
        ok: action.ok === undefined ? null : Boolean(action.ok),
        version: String(action.version || ""),
        checkedAt: String(action.checkedAt || "")
      });
    default:
      return state;
  }
}

/* ===================================================================
 * 纯选择器：state → 视图数据（不触碰 DOM，可脱离 jsdom 单测）
 * =================================================================== */

/** 解析「仅扫描选中报社」的实际名单；selected=null 视为全选。 */
export function selectScanNames(scan) {
  if (!scan) return [];
  if (!scan.selected) return (scan.paperNames || []).slice();
  return scan.paperNames.filter(function (name) { return scan.selected.has(name); });
}

export function selectFiltered(state) {
  if (!state || !state.data) return [];
  var q = state.q.trim().toLowerCase();
  var newUrls = state.newOnly ? new Set(state.data.new_urls || []) : null;
  return state.data.records.filter(function (r) {
    if (newUrls && !newUrls.has(r.url)) return false;
    if (!state.papers.has(r.paper)) return false;
    if (!state.keywords.size || !(r.matched || []).some(function (k) { return state.keywords.has(k); })) return false;
    if (state.from && (!r.date || r.date < state.from)) return false;
    if (state.to && (!r.date || r.date > state.to)) return false;
    if (q) {
      var hay = (r.title + " " + r.section + " " + (r.snippets || []).join(" ")).toLowerCase();
      if (hay.indexOf(q) === -1) return false;
    }
    if (state.linkedFilter) {
      var f = state.linkedFilter;
      if (f.type === "date" && r.date !== f.date) return false;
      if (f.type === "cell" && (r.date !== f.date || r.paper !== f.paper)) return false;
      if (f.type === "layout" && !f.urls.has(r.url)) return false;
    }
    return true;
  });
}

/** 「本次新增」URL 集合；没有快照时为无法命中的空集。 */
export function newUrlSet(data) {
  return new Set((data && data.new_urls) || []);
}

/** 归档里属于「本次新增」的记录数（供筛选 chip 显示，不受其他筛选影响）。 */
export function countNewHits(state) {
  if (!state || !state.data) return 0;
  var urls = state.data.new_urls;
  if (!urls || !urls.length) return 0;
  var set = new Set(urls);
  var count = 0;
  state.data.records.forEach(function (r) { if (set.has(r.url)) count += 1; });
  return count;
}

function byDateDesc(a, b) {
  if (a.date !== b.date) return a.date < b.date ? 1 : -1;
  return a.url < b.url ? 1 : (a.url > b.url ? -1 : 0);
}

export function selectSorted(state) {
  var list = selectFiltered(state);
  var cmp = {
    "date-desc": byDateDesc,
    "date-asc": function (a, b) { return -byDateDesc(a, b); },
    "paper": function (a, b) { return a.paper.localeCompare(b.paper, "zh") || byDateDesc(a, b); },
    "title": function (a, b) { return a.title.localeCompare(b.title, "zh"); }
  }[state.sort] || byDateDesc;
  return list.slice().sort(cmp);
}

/** 导出用的受控筛选（与列表/图表保持一致；图表关联筛选优先于日期输入）。 */
export function selectExportFilters(state) {
  var f = state.linkedFilter;
  var papers = Array.from(state.papers);
  var from = state.from;
  var to = state.to;
  var urls = null;
  if (f && f.type === "date") { from = f.date; to = f.date; }
  if (f && f.type === "layout") urls = Array.from(f.urls);
  if (f && f.type === "cell") { papers = [f.paper]; from = f.date; to = f.date; }
  return {
    papers: papers,
    keywords: Array.from(state.keywords),
    urls: urls,
    from: from,
    to: to,
    query: state.q.trim(),
    sort: state.sort
  };
}

/** 全量归档的报社命中共计排名（后端 paper_counts，按命中数倒序）。 */
export function paperRanking(data) {
  if (!data || !data.paper_counts) return [];
  return Object.keys(data.paper_counts).map(function (name) {
    return { name: name, count: data.paper_counts[name] };
  }).sort(function (a, b) {
    return b.count - a.count || a.name.localeCompare(b.name, "zh");
  });
}

/** 当前筛选结果内，各命中关键词的报道数排名。 */
export function keywordRanking(records) {
  var counts = {};
  (records || []).forEach(function (r) {
    (r.matched || []).forEach(function (k) { counts[k] = (counts[k] || 0) + 1; });
  });
  return Object.keys(counts).map(function (name) {
    return { name: name, count: counts[name] };
  }).sort(function (a, b) {
    return b.count - a.count || a.name.localeCompare(b.name, "zh");
  });
}

export var HEATMAP_KEY_SEP = "\u0001";

/** 日期 × 报社 命中矩阵：行取命中最多的 N 家报社，列取最近的 N 天。 */
export function heatmap(records, maxPapers, maxDates) {
  var cellCounts = {};
  var paperTotals = {};
  var dateSet = {};
  (records || []).forEach(function (r) {
    if (!r.paper || !r.date) return;
    var key = r.paper + HEATMAP_KEY_SEP + r.date;
    cellCounts[key] = (cellCounts[key] || 0) + 1;
    paperTotals[r.paper] = (paperTotals[r.paper] || 0) + 1;
    dateSet[r.date] = true;
  });
  var papers = Object.keys(paperTotals).sort(function (a, b) {
    return paperTotals[b] - paperTotals[a] || a.localeCompare(b, "zh");
  }).slice(0, maxPapers || 8);
  var dates = Object.keys(dateSet).sort();
  if (maxDates && dates.length > maxDates) dates = dates.slice(dates.length - maxDates);
  var max = 0;
  Object.keys(cellCounts).forEach(function (key) { if (cellCounts[key] > max) max = cellCounts[key]; });
  return { papers: papers, dates: dates, cells: cellCounts, max: max || 1 };
}

export function createStore(reduce, initialState) {
  var current = initialState;
  var listeners = [];
  return {
    getState: function () { return current; },
    dispatch: function (action) {
      current = reduce(current, action);
      listeners.slice().forEach(function (fn) { fn(current); });
      return current;
    },
    subscribe: function (fn) {
      listeners.push(fn);
      return function () {
        var i = listeners.indexOf(fn);
        if (i !== -1) listeners.splice(i, 1);
      };
    }
  };
}

/** 可撤销的有效筛选；日期快捷项与其日期范围合并为一项。 */
export function selectActiveFilters(state) {
  var filters = [];
  if (state.q.trim()) filters.push({ key: "query", label: "搜索：" + state.q.trim() });
  if (state.from || state.to) filters.push({ key: "dates", label: "日期：" + (state.from || "不限") + " 至 " + (state.to || "不限") });
  var data = state.data;
  if (data) {
    if (state.papers.size !== data.papers.length) filters.push({ key: "papers", label: "报社：" + (Array.from(state.papers).join("、") || "未选择") });
    if (state.focusKeywords || state.keywords.size !== data.keywords.length) filters.push({ key: "keywords", label: (state.focusKeywords ? "当前关键词：" : "关键词：") + ((state.focusKeywords || Array.from(state.keywords)).join("、") || "未选择") });
  }
  if (state.newOnly) filters.push({ key: "newOnly", label: "只看本次新增" });
  if (state.linkedFilter) filters.push({ key: "linked", label: "图表：" + state.linkedFilter.label });
  return filters;
}
