/* 前端状态：严格 action + 纯 reducer（单向数据流）。
 *
 * 唯一变更入口是 store.dispatch(action)：reducer(state, action) 计算**新的** state，
 * 再通知订阅者重渲染。reducer 是纯函数（不读写外部变量，Set 一律产生新实例），
 * 因此可以脱离 DOM 单独测试。零框架、零构建、零第三方依赖；供 app.js 使用。
 */

export var INITIAL_ROWS = 50;
export var ROW_STEP = 50;

// action 类型常量：避免散落的字符串字面量拼错。
export var A = {
  LOAD_DATA: "LOAD_DATA",
  SET_QUERY: "SET_QUERY",
  SET_DATE_RANGE: "SET_DATE_RANGE",
  SET_SORT: "SET_SORT",
  SET_PRESET: "SET_PRESET",
  SET_LINKED: "SET_LINKED",
  CLEAR_LINKED: "CLEAR_LINKED",
  TOGGLE_PAPER: "TOGGLE_PAPER",
  TOGGLE_KEYWORD: "TOGGLE_KEYWORD",
  SHOW_ALL_HISTORY: "SHOW_ALL_HISTORY",
  RESET_VIEW: "RESET_VIEW",
  LOAD_MORE: "LOAD_MORE"
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
    visibleRows: INITIAL_ROWS
  };
}

function withPatch(state, patch) {
  return Object.assign({}, state, patch);
}

function toggledSet(source, name, on) {
  var next = new Set(source);
  if (on) next.add(name); else next.delete(name);
  return next;
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
    case A.TOGGLE_PAPER:
      return withPatch(state, { papers: toggledSet(state.papers, action.name, action.on) });
    case A.TOGGLE_KEYWORD:
      return withPatch(state, {
        keywords: toggledSet(state.keywords, action.name, action.on),
        focusKeywords: null
      });
    case A.SHOW_ALL_HISTORY:
      return withPatch(state, {
        focusKeywords: null,
        keywords: new Set(state.data.keywords),
        linkedFilter: null,
        visibleRows: INITIAL_ROWS
      });
    case A.RESET_VIEW: {
      var reset = {
        linkedFilter: null, q: "", from: "", to: "",
        sort: "date-desc", preset: "all", visibleRows: INITIAL_ROWS
      };
      if (state.data) {
        reset.papers = new Set(state.data.papers);
        reset.keywords = new Set(state.focusKeywords || state.data.keywords);
      }
      return withPatch(state, reset);
    }
    case A.LOAD_MORE:
      return withPatch(state, { visibleRows: state.visibleRows + ROW_STEP });
    default:
      return state;
  }
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
