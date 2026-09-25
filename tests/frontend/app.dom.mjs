/* app.js 的 DOM 行为测试用例（jsdom，不联网）。
 *
 * 以「名称 + 异步函数」列表导出，由 run-dom.mjs 驱动执行。
 * 之所以不用 node:test：jsdom 会留下无法 window.close() 的句柄，node:test 的
 * 进程隔离会因此挂到超时（本机 Node 22，jsdom 24/30 均如此）；自研 driver 可直接
 * 在结束时 process.exit，且失败判定完全可控。
 */
import assert from "node:assert/strict";

import { boot, defaultRoutes, runningScan, settle } from "./harness.mjs";

function rows(doc) {
  return doc.querySelectorAll("#rows tr");
}

async function withPage(options, fn) {
  const page = await boot(options);
  try {
    return await fn(page);
  } finally {
    page.close();
  }
}

export const domTests = [
  {
    name: "首屏渲染：概览、版本、命中行与版面位置",
    fn: () =>
      withPage(undefined, async (page) => {
        assert.match(page.text("meta"), /共 2 条命中/);
        assert.equal(page.text("version"), "v0.2.0");
        assert.equal(rows(page.document).length, 2);

        const firstRow = rows(page.document)[0];
        assert.match(firstRow.textContent, /湛江日报/);
        assert.match(firstRow.textContent, /版内 8\/11/);
        assert.match(firstRow.textContent, /上版 70%/);
        assert.equal(firstRow.querySelector("a").getAttribute("href"), "http://example.test/zj/1");
      }),
  },
  {
    name: "报社列表与关键词 chips 由接口数据渲染（零 innerHTML）",
    fn: () =>
      withPage(undefined, async (page) => {
        assert.equal(page.document.querySelectorAll("#papers-list .papers-row").length, 2);
        assert.match(page.document.getElementById("papers-list").textContent, /内置/);
        const chips = [...page.document.querySelectorAll("#keyword-chips .chip")];
        assert.ok(chips.some((c) => c.textContent.includes("广东海洋大学")));
      }),
  },
  {
    name: "搜索框过滤命中行（跨过 140ms 渲染防抖）",
    fn: () =>
      withPage(undefined, async (page) => {
        const q = page.document.getElementById("q");
        q.value = "人工智能";
        q.dispatchEvent(new page.window.Event("input", { bubbles: true }));
        await settle();
        assert.equal(rows(page.document).length, 1);
        assert.match(rows(page.document)[0].textContent, /光明日报/);
      }),
  },
  {
    name: "扫描范围模式切换：显示日期框、隐藏天数框",
    fn: () =>
      withPage(undefined, async (page) => {
        const mode = page.document.getElementById("scan-mode");
        mode.value = "range";
        mode.dispatchEvent(new page.window.Event("change", { bubbles: true }));
        await page.flush();
        assert.equal(page.document.getElementById("scan-since-field").hidden, false);
        assert.equal(page.document.getElementById("scan-until-field").hidden, false);
        assert.equal(page.document.getElementById("scan-days-field").hidden, true);
      }),
  },
  {
    name: "报社 chip 取消勾选后结果随之减少",
    fn: () =>
      withPage(undefined, async (page) => {
        const chip = [...page.document.querySelectorAll("#paper-chips .chip")].find((c) =>
          c.textContent.includes("光明日报")
        );
        assert.ok(chip, "应有「光明日报」chip");
        chip.click();
        await settle();
        assert.equal(rows(page.document).length, 1);
        assert.match(rows(page.document)[0].textContent, /湛江日报/);
      }),
  },
  {
    name: "快捷区间：点「今天」后日期框与结果同步",
    fn: () =>
      withPage(undefined, async (page) => {
        page.document.querySelector('#preset-chips [data-preset="today"]').click();
        await settle();
        assert.equal(page.document.getElementById("from").value, "2026-09-23");
        assert.equal(page.document.getElementById("to").value, "2026-09-23");
        assert.equal(rows(page.document).length, 1);
      }),
  },
  {
    name: "提交扫描：POST /api/scan 并轮询至完成态",
    fn: () => {
      let polls = 0;
      const routes = (path, init) => {
        const method = (init?.method || "GET").toUpperCase();
        if (method === "POST" && path === "/api/scan") return { status: 202, body: runningScan() };
        if (method === "GET" && path.startsWith("/api/scan") && !path.startsWith("/api/scan/history")) {
          polls += 1;
          if (polls >= 2) {
            return {
              body: runningScan({
                status: "done",
                exit_code: 0,
                summary: { hits: 2, new_hits: 1 },
                progress: { completed: 2, total: 2, percent: 100, current: "" },
              }),
            };
          }
          return { body: runningScan({ progress: { completed: 1, total: 2, percent: 50, current: "" } }) };
        }
        return defaultRoutes(path, init);
      };
      return withPage({ routes }, async (page) => {
        page.document
          .getElementById("scan-form")
          .dispatchEvent(new page.window.Event("submit", { bubbles: true, cancelable: true }));
        await page.flush(4);

        const posted = page.calls.find((c) => c.method === "POST" && c.path === "/api/scan");
        assert.ok(posted, "应提交 POST /api/scan");
        assert.ok(Array.isArray(posted.body.keywords) && posted.body.keywords.length, "body 应带 keywords");

        for (let i = 0; i < 8; i += 1) await page.flush(3);
        assert.match(page.text("scan-status"), /扫描完成/);
      });
    },
  },
];
