/* 前端纯函数单元测试（Node 内置 test runner，零第三方依赖）。
 *
 * 运行：node --test tests/frontend/format.test.mjs
 * 依赖 src/newspaper_auto/static/package.json 声明 type=module，使同目录 .js 被当作 ESM。
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  ISO_DATE_RE,
  csvCell,
  geomText,
  parseInt10,
  shiftDate,
  spanDays,
  splitKeywords,
  todayIso,
} from "../../src/newspaper_auto/static/format.js";

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

test("splitKeywords 去重去空并支持中英文分隔", () => {
  assert.deepEqual(splitKeywords("广东海洋大学, 海大、广海\n海大"), ["广东海洋大学", "海大", "广海"]);
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
