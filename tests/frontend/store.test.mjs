/* store.js 的纯 reducer 单测：action → 新 state，不碰 DOM、不联网、不依赖 jsdom。 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  A,
  INITIAL_ROWS,
  ROW_STEP,
  createInitialState,
  reducer,
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
