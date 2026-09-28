/* 前端纯函数单元测试（Node 内置 test runner，零第三方依赖）。
 *
 * 运行：node --test tests/frontend/format.test.mjs
 * 依赖 src/newspaper_auto/static/package.json 声明 type=module，使同目录 .js 被当作 ESM。
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  CONTEXT_MAX_CHARS,
  ISO_DATE_RE,
  contextText,
  csvCell,
  geomText,
  parseInt10,
  shiftDate,
  spanDays,
  splitKeywords,
  todayIso,
} from "../../src/newspaper_auto/static/format.js";
import { pct } from "../../src/newspaper_auto/static/render.js";
import { escapeHtml } from "../../src/newspaper_auto/static/dom.js";

test("shiftDate 跨月/跨年并保持 ISO 补零", () => {
  assert.equal(shiftDate("2026-03-01", -1), "2026-02-28");
  assert.equal(shiftDate("2026-01-01", -1), "2025-12-31");
  assert.equal(shiftDate("2026-09-23", 1), "2026-09-24");
});

test("spanDays 含端点", () => {
  assert.equal(spanDays("2026-09-23", "2026-09-23"), 1);
  assert.equal(spanDays("2026-09-01", "2026-09-30"), 30);
});

test("csvCell 防公式注入并转义引号/逗号", () => {
  assert.equal(csvCell("=1+1"), "'=1+1");
  assert.equal(csvCell("+x"), "'+x");
  assert.equal(csvCell('a"b'), '"a""b"');
  assert.equal(csvCell("a,b"), '"a,b"');
  assert.equal(csvCell(null), "");
  assert.equal(csvCell(undefined), "");
});

test("geomText 渲染版内位置并容忍旧归档", () => {
  assert.equal(geomText({ index: 2, total: 7, top: 0.38 }), "版内 2/7 · 上版 38%");
  assert.equal(geomText({ index: 1, total: 3 }), "版内 1/3");
  assert.equal(geomText(null), "");
  assert.equal(geomText("nope"), "");
});

test("contextText 折叠空白并把 CSV 上下文压到上限", () => {
  // 回归：上游每个片段是命中位置前后各 70 字，一条命中最多 3 段；直接拼接后
  // 每行 150～400 字，导出表被撑得没法看。
  var long = "。".repeat(200);
  assert.equal(contextText([long]).length, CONTEXT_MAX_CHARS + 1, "截断后只多一个省略号");
  assert.ok(contextText([long]).endsWith("…"));
  // 未超限时不加省略号
  assert.equal(contextText(["短片段"]), "短片段");
  // 多段之间用空格连接，空白折叠成单空格（换行不能漏进 CSV 单元格）
  assert.equal(contextText(["a\n\nb", "  c  "]), "a b c");
  // 边界：恰好等于上限不加省略号
  assert.equal(contextText(["x".repeat(CONTEXT_MAX_CHARS)]), "x".repeat(CONTEXT_MAX_CHARS));
  assert.equal(contextText(["x".repeat(CONTEXT_MAX_CHARS + 1)]).length, CONTEXT_MAX_CHARS + 1);
  // 截断处不留尾随空格
  assert.equal(contextText(["a".repeat(30) + "     " + "b".repeat(60)], 30), "a".repeat(30) + "…");
  // 缺字段 / 空值 / 非数组都要静默降级
  assert.equal(contextText(null), "");
  assert.equal(contextText(undefined), "");
  assert.equal(contextText([]), "");
  assert.equal(contextText([null, undefined, ""]), "");
  assert.equal(contextText(["x"], 0), "x", "非正数上限回落到默认值");
});

test("splitKeywords 去重去空并支持中英文分隔", () => {
  assert.deepEqual(splitKeywords("示例大学, 例大、示大\n例大"), ["示例大学", "例大", "示大"]);
  assert.deepEqual(splitKeywords("   "), []);
});

test("parseInt10 只接受整数", () => {
  assert.equal(parseInt10("8"), 8);
  assert.equal(parseInt10(8), 8);
  assert.ok(Number.isNaN(parseInt10("8.5")));
  assert.ok(Number.isNaN(parseInt10("")));
  assert.ok(Number.isNaN(parseInt10("x")));
});

test("todayIso 形如 YYYY-MM-DD", () => {
  assert.match(todayIso(), ISO_DATE_RE);
});

test("pct 守卫非有限值并按 0~100% 夹取", () => {
  assert.equal(pct(0.5), "50.00%");
  assert.equal(pct(0), "0.00%");
  assert.equal(pct(2), "100.00%");
  assert.equal(pct(-1), "0.00%");
  assert.equal(pct(undefined), "0%");
  assert.equal(pct(null), "0%");
  assert.equal(pct(NaN), "0%");
  assert.equal(pct(Infinity), "0%");
});

test("escapeHtml 转义危险字符（零 innerHTML 策略下的兜底工具）", () => {
  assert.equal(escapeHtml('<a href="x">&\'</a>'), "&lt;a href=&quot;x&quot;&gt;&amp;&#39;&lt;/a&gt;");
  assert.equal(escapeHtml(null), "");
  assert.equal(escapeHtml(undefined), "");
});
