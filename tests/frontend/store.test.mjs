/* store.js 的纯 reducer 单测：action → 新 state，不碰 DOM、不联网、不依赖 jsdom。 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  A,
  HEATMAP_KEY_SEP,
  INITIAL_ROWS,
  ROW_STEP,
  countNewHits,
  createInitialState,
  heatmap,
  keywordRanking,
  newUrlSet,
  paperRanking,
  reducer,
  selectFiltered,
  selectScanNames,
} from "../../src/newspaper_auto/static/store.js";

function sampleData() {
  return {
    papers: ["湛江日报", "光明日报"],
    keywords: ["k1", "k2"],
    configured_keywords: [],
    date_counts: {},
    records: [],
  };
}

test("初始状态：空数据、全选空集、默认日期倒序", () => {
  const s = createInitialState();
  assert.equal(s.data, null);
  assert.equal(s.q, "");
  assert.equal(s.papers.size, 0);
  assert.equal(s.keywords.size, 0);
  assert.equal(s.focusKeywords, null);
  assert.equal(s.sort, "date-desc");
  assert.equal(s.preset, "all");
  assert.equal(s.visibleRows, INITIAL_ROWS);
});

test("SET_QUERY：写入关键词并清掉关联筛选与展开量", () => {
  const base = Object.assign(createInitialState(), { linkedFilter: { type: "date", date: "2026-01-01" }, visibleRows: 999 });
  const s = reducer(base, { type: A.SET_QUERY, value: "人工智能" });
  assert.equal(s.q, "人工智能");
  assert.equal(s.linkedFilter, null);
  assert.equal(s.visibleRows, INITIAL_ROWS);
});

test("TOGGLE_PAPER：产生新 Set，不改动原状态", () => {
  const s0 = createInitialState();
  const s1 = reducer(s0, { type: A.TOGGLE_PAPER, name: "光明日报", on: true });
  assert.equal(s0.papers.size, 0, "原 state 不得被就地修改");
  assert.notEqual(s0.papers, s1.papers, "应返回新 Set 实例");
  assert.deepEqual([...s1.papers], ["光明日报"]);

  const s2 = reducer(s1, { type: A.TOGGLE_PAPER, name: "光明日报", on: false });
  assert.deepEqual([...s2.papers], []);
});

test("TOGGLE_KEYWORD：切换关键词并清空 focusKeywords", () => {
  const base = Object.assign(createInitialState(), { focusKeywords: ["k1"] });
  const s = reducer(base, { type: A.TOGGLE_KEYWORD, name: "k2", on: true });
  assert.deepEqual([...s.keywords].sort(), ["k2"]);
  assert.equal(s.focusKeywords, null);
});

test("SET_PRESET：写入区间并复位展开量", () => {
  const base = Object.assign(createInitialState(), { visibleRows: 999, linkedFilter: { type: "date", date: "x" } });
  const s = reducer(base, { type: A.SET_PRESET, preset: "7d", from: "2026-09-17", to: "2026-09-23" });
  assert.equal(s.preset, "7d");
  assert.equal(s.from, "2026-09-17");
  assert.equal(s.to, "2026-09-23");
  assert.equal(s.visibleRows, INITIAL_ROWS);
  assert.equal(s.linkedFilter, null);
});

test("SET_DATE_RANGE：置为 custom 并只改给到的端点", () => {
  const base = Object.assign(createInitialState(), { to: "2026-09-23" });
  const s = reducer(base, { type: A.SET_DATE_RANGE, from: "2026-09-01" });
  assert.equal(s.preset, "custom");
  assert.equal(s.from, "2026-09-01");
  assert.equal(s.to, "2026-09-23", "未提供的端点应保持原值");
});

test("LOAD_MORE：按 ROW_STEP 递增", () => {
  const s = reducer(createInitialState(), { type: A.LOAD_MORE });
  assert.equal(s.visibleRows, INITIAL_ROWS + ROW_STEP);
});

test("LOAD_DATA(reset)：按 requestedFocus 聚焦关键词并复位视图", () => {
  const base = Object.assign(createInitialState(), { q: "x", preset: "7d", visibleRows: 999 });
  const s = reducer(base, {
    type: A.LOAD_DATA,
    data: sampleData(),
    mode: "reset",
    requestedFocus: ["k1"],
    allPapersSelected: false,
    allKeywordsSelected: false,
  });
  assert.deepEqual([...s.papers], ["湛江日报", "光明日报"]);
  assert.deepEqual([...s.keywords], ["k1"]);
  assert.deepEqual(s.focusKeywords, ["k1"]);
  assert.equal(s.q, "");
  assert.equal(s.preset, "all");
  assert.equal(s.visibleRows, INITIAL_ROWS);
});

test("LOAD_DATA(preserve)：保留已选中的报纸/关键词子集", () => {
  const base = Object.assign(createInitialState(), {
    papers: new Set(["湛江日报"]),
    keywords: new Set(["k2"]),
    focusKeywords: null,
  });
  const s = reducer(base, {
    type: A.LOAD_DATA,
    data: sampleData(),
    mode: "preserve",
    requestedFocus: null,
    allPapersSelected: false,
    allKeywordsSelected: false,
  });
  assert.deepEqual([...s.papers], ["湛江日报"], "只保留仍存在的已选报纸");
  assert.deepEqual([...s.keywords], ["k2"]);
});

test("RESET_VIEW：有数据时恢复全选并复位筛选", () => {
  const base = Object.assign(createInitialState(), {
    data: sampleData(),
    papers: new Set(["湛江日报"]),
    keywords: new Set(["k1"]),
    focusKeywords: null,
    q: "x",
    preset: "30d",
  });
  const s = reducer(base, { type: A.RESET_VIEW });
  assert.deepEqual([...s.papers], ["湛江日报", "光明日报"]);
  assert.deepEqual([...s.keywords], ["k1", "k2"]);
  assert.equal(s.q, "");
  assert.equal(s.preset, "all");
  assert.equal(s.visibleRows, INITIAL_ROWS);
});

test("未知 action：原样返回同一引用", () => {
  const s0 = createInitialState();
  assert.equal(reducer(s0, { type: "NOT_A_REAL_ACTION" }), s0);
});

/* ----------------------------- 新增切片：scan / papersPanel / exportDialog / system ----------------------------- */

test("初始状态含 scan/papersPanel/exportDialog/system 切片且 filter.papers 仍是 Set", () => {
  const s = createInitialState();
  assert.ok(s.papers instanceof Set, "filter 用的 papers 必须是 Set");
  assert.equal(s.scan.running, false);
  assert.equal(s.scan.selected, null);
  assert.equal(s.scan.pendingOnly, null);
  assert.deepEqual(s.scan.logLines, []);
  assert.equal(s.scan.history, null);
  assert.equal(s.papersPanel.view, null);
  assert.equal(s.exportDialog.busy, false);
  assert.equal(s.system.ok, null);
});

test("SCAN_CONFIG_ONLY：报社未到位先暂存，到位后套用（空交集保留空集合）", () => {
  let s = reducer(createInitialState(), { type: A.SCAN_CONFIG_ONLY, only: "光明日报" });
  assert.equal(s.scan.pendingOnly, "光明日报");
  s = reducer(s, { type: A.SCAN_SET_PAPERS, names: ["光明日报", "南方日报"] });
  assert.deepEqual([...s.scan.selected], ["光明日报"]);
  assert.equal(s.scan.pendingOnly, null);

  // 显式配置但交集为空：保留空 Set，不得静默回落成“全选”
  let s2 = reducer(createInitialState(), { type: A.SCAN_CONFIG_ONLY, only: "不存在的报" });
  s2 = reducer(s2, { type: A.SCAN_SET_PAPERS, names: ["光明日报"] });
  assert.ok(s2.scan.selected instanceof Set);
  assert.equal(s2.scan.selected.size, 0);
});

test("SCAN_SET_PAPERS：保留仍存在的已选报社，且不修改旧 Set", () => {
  const s0 = reducer(createInitialState(), { type: A.SCAN_SET_PAPERS, names: ["A", "B", "C"] });
  assert.equal(s0.scan.selected, null, "无显式选择时 selected=null 表示全选");
  const s1 = reducer(s0, { type: A.SCAN_TOGGLE_PAPER, name: "B", on: false });
  const s2 = reducer(s1, { type: A.SCAN_SET_PAPERS, names: ["A", "C", "D"] });
  assert.deepEqual([...s2.scan.selected].sort(), ["A", "C"]);
  assert.notEqual(s1.scan.selected, s2.scan.selected);
  assert.deepEqual([...s1.scan.selected].sort(), ["A", "C"], "旧 state 的 Set 不得被就地修改");
});

test("SCAN_TOGGLE_PAPER：selected=null 时从全量起算", () => {
  const base = reducer(createInitialState(), { type: A.SCAN_SET_PAPERS, names: ["A", "B"] });
  const s = reducer(base, { type: A.SCAN_TOGGLE_PAPER, name: "A", on: false });
  assert.deepEqual([...s.scan.selected], ["B"]);
});

test("SCAN_INGEST：追加日志、变更任务重置换行、上限 300 行", () => {
  const long = Array.from({ length: 301 }, (_, i) => "L" + i);
  let s = reducer(createInitialState(), { type: A.SCAN_INGEST, task: { task_id: "t1", log: long, log_cursor: 10, progress: { total: 3 } } });
  assert.equal(s.scan.taskId, "t1");
  assert.equal(s.scan.logLines.length, 300);
  assert.equal(s.scan.logCursor, 10);
  assert.deepEqual(s.scan.progress, { total: 3 });

  s = reducer(s, { type: A.SCAN_INGEST, task: { task_id: "t2", log: ["new"], log_cursor: 2 } });
  assert.equal(s.scan.taskId, "t2");
  assert.deepEqual(s.scan.logLines, ["new"]);
  assert.equal(s.scan.logCursor, 2);
  const base = createInitialState();
  reducer(base, { type: A.SCAN_INGEST, task: { task_id: "x", log: ["a"] } });
  assert.deepEqual(base.scan.logLines, [], "reducer 不得就地修改原 state");
});

test("SCAN_RUNNING / SCAN_CANCELLING / SCAN_STATUS / SCAN_HISTORY", () => {
  let s = reducer(createInitialState(), { type: A.SCAN_RUNNING, running: true });
  assert.equal(s.scan.running, true);
  s = reducer(s, { type: A.SCAN_CANCELLING, cancelling: true });
  assert.equal(s.scan.cancelling, true);
  s = reducer(s, { type: A.SCAN_RUNNING, running: false });
  assert.equal(s.scan.cancelling, false);
  s = reducer(s, { type: A.SCAN_STATUS, text: "扫描完成", tone: "success" });
  assert.deepEqual(s.scan.status, { text: "扫描完成", tone: "success" });
  s = reducer(s, { type: A.SCAN_HISTORY, tasks: [{ task_id: "a" }] });
  assert.equal(s.scan.history.length, 1);
});

test("SET_KEYWORDS：整组替换关键词并清关联筛选", () => {
  const base = Object.assign(createInitialState(), { linkedFilter: { type: "date", date: "x" } });
  const s = reducer(base, { type: A.SET_KEYWORDS, names: ["k2"] });
  assert.deepEqual([...s.keywords], ["k2"]);
  assert.equal(s.linkedFilter, null);
});

test("papersPanel 与 exportDialog / system 切片 reducer", () => {
  let s = reducer(createInitialState(), { type: A.PAPERS_VIEW, view: { items: [1] } });
  assert.deepEqual(s.papersPanel.view.items, [1]);
  s = reducer(s, { type: A.PAPERS_STATUS, text: "已保存", tone: "success" });
  assert.equal(s.papersPanel.status, "已保存");
  assert.equal(s.papersPanel.tone, "success");
  s = reducer(s, { type: A.PAPERS_HINT, text: "试抓中…" });
  assert.equal(s.papersPanel.hint, "试抓中…");
  s = reducer(s, { type: A.PAPERS_EDITING, name: "光明日报" });
  assert.equal(s.papersPanel.editing, "光明日报");

  s = reducer(s, { type: A.EXPORT_BUSY, busy: true });
  assert.equal(s.exportDialog.busy, true);
  s = reducer(s, { type: A.EXPORT_STATUS, text: "错了", tone: "error" });
  assert.equal(s.exportDialog.status, "错了");
  assert.equal(s.exportDialog.tone, "error");

  s = reducer(s, { type: A.SYSTEM_LOADING, loading: true });
  assert.equal(s.system.loading, true);
  s = reducer(s, { type: A.SYSTEM_ERROR, message: "boom" });
  assert.equal(s.system.error, "boom");
  s = reducer(s, { type: A.SYSTEM_HEALTH, ok: true, version: "9.9.9" });
  assert.equal(s.system.ok, true);
  assert.equal(s.system.version, "9.9.9");
});

/* ----------------------------- 纯选择器 ----------------------------- */

test("selectScanNames：null=全选，Set=子集，空集=空", () => {
  assert.deepEqual(selectScanNames({ paperNames: ["A", "B"], selected: null }), ["A", "B"]);
  assert.deepEqual(selectScanNames({ paperNames: ["A", "B"], selected: new Set(["B"]) }), ["B"]);
  assert.deepEqual(selectScanNames({ paperNames: ["A", "B"], selected: new Set() }), []);
});

test("paperRanking / keywordRanking 按命中数倒序", () => {
  assert.deepEqual(paperRanking({ paper_counts: { A: 2, B: 5 } }).map((x) => x.name), ["B", "A"]);
  const rank = keywordRanking([
    { matched: ["k1", "k2"] },
    { matched: ["k1"] },
  ]);
  assert.deepEqual(rank, [{ name: "k1", count: 2 }, { name: "k2", count: 1 }]);
});

test("SET_FILTERS_OPEN：控制结果区筛选浮层开合", () => {
  const s = createInitialState();
  assert.equal(s.filtersOpen, false, "默认收起：浮层不占版面");
  assert.equal(reducer(s, { type: A.SET_FILTERS_OPEN, open: true }).filtersOpen, true);
  assert.equal(reducer(s, { type: A.SET_FILTERS_OPEN, open: false }).filtersOpen, false);
  assert.equal(reducer(s, { type: A.SET_FILTERS_OPEN }).filtersOpen, false, "缺省视为收起");
});

test("heatmap：行列裁剪与计数", () => {
  const records = [
    { paper: "A", date: "2026-01-02" },
    { paper: "A", date: "2026-01-02" },
    { paper: "B", date: "2026-01-01" },
  ];
  const h = heatmap(records, 8, 14);
  assert.deepEqual(h.papers, ["A", "B"]);
  assert.deepEqual(h.dates, ["2026-01-01", "2026-01-02"]);
  assert.equal(h.cells["A" + HEATMAP_KEY_SEP + "2026-01-02"], 2);
  assert.equal(h.max, 2);
});

test("selectFiltered：支持按日期/版面/单元格关联筛选，并容忍缺失 snippets", () => {
  const base = Object.assign(createInitialState(), {
    data: {
      papers: ["A"], keywords: ["k"], date_counts: {},
      records: [
        { paper: "A", date: "2026-01-01", title: "t1", section: "", url: "u1", matched: ["k"], snippets: undefined },
        { paper: "A", date: "2026-01-02", title: "t2", section: "", url: "u2", matched: ["k"], snippets: ["s"] },
      ],
    },
    papers: new Set(["A"]),
    keywords: new Set(["k"]),
  });
  assert.equal(selectFiltered(base).length, 2, "缺失 snippets 不应抛错");
  const cell = Object.assign(base, { linkedFilter: { type: "cell", key: "x", paper: "A", date: "2026-01-02" } });
  assert.deepEqual(selectFiltered(cell).map((r) => r.url), ["u2"]);
  const layout = Object.assign(base, { linkedFilter: { type: "layout", key: "x", urls: new Set(["u1"]) } });
  assert.deepEqual(selectFiltered(layout).map((r) => r.url), ["u1"]);
});

/* ------------------------------------------------------- 只看本次新增 */
test("SET_NEW_ONLY：切换只看新增并复位展开量，RESET_VIEW 会关掉它", () => {
  const s0 = Object.assign(createInitialState(), { visibleRows: 999 });
  const on = reducer(s0, { type: A.SET_NEW_ONLY, on: true });
  assert.equal(on.newOnly, true);
  assert.equal(on.visibleRows, INITIAL_ROWS, "改筛选要复位「显示更多」的展开量");
  assert.equal(s0.newOnly, false, "原 state 不得被就地修改");

  assert.equal(reducer(on, { type: A.SET_NEW_ONLY, on: false }).newOnly, false);
  assert.equal(reducer(on, { type: A.SET_NEW_ONLY }).newOnly, false, "缺省 on 视为关闭");
  assert.equal(reducer(on, { type: A.RESET_VIEW }).newOnly, false, "清除筛选要一并关掉只看新增");
});

test("selectFiltered：newOnly 只保留 new_urls 里的记录", () => {
  const state = Object.assign(createInitialState(), {
    data: {
      papers: ["A"], keywords: ["k"], date_counts: {},
      new_urls: ["u2"],
      records: [
        { paper: "A", date: "2026-01-01", title: "t1", section: "", url: "u1", matched: ["k"], snippets: [] },
        { paper: "A", date: "2026-01-02", title: "t2", section: "", url: "u2", matched: ["k"], snippets: [] },
      ],
    },
    papers: new Set(["A"]),
    keywords: new Set(["k"]),
    newOnly: true,
  });
  assert.deepEqual(selectFiltered(state).map((r) => r.url), ["u2"]);
  assert.equal(selectFiltered(Object.assign({}, state, { newOnly: false })).length, 2);
  // 打开只看新增但没有快照 → 空结果，而不是悄悄退回全量
  const noSnapshot = Object.assign({}, state, { data: Object.assign({}, state.data, { new_urls: [] }) });
  assert.deepEqual(selectFiltered(noSnapshot), []);
});

test("countNewHits：只统计归档里确实存在的新增 URL", () => {
  const data = {
    new_urls: ["u1", "u-missing"],
    records: [
      { url: "u1" }, { url: "u2" }, { url: "u1" },
    ],
  };
  assert.equal(countNewHits({ data }), 2, "u1 出现两次应计两次，缺失的新增 URL 不计");
  assert.equal(countNewHits({ data: { new_urls: [], records: [{ url: "u1" }] } }), 0);
  assert.equal(countNewHits({ data: null }), 0);
  assert.deepEqual([...newUrlSet(data)], ["u1", "u-missing"]);
  assert.equal(newUrlSet(null).size, 0, "没有数据时返回空集而非抛错");
});

