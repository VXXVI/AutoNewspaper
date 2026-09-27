/* app.js 的 DOM 行为测试用例（jsdom，不联网）。
 *
 * 以「名称 + 异步函数」列表导出，由 run-dom.mjs 驱动执行。
 * 之所以不用 node:test：jsdom 会留下无法 window.close() 的句柄，node:test 的
 * 进程隔离会因此挂到超时（本机 Node 22，jsdom 24/30 均如此）；自研 driver 可直接
 * 在结束时 process.exit，且失败判定完全可控。
 */
import assert from "node:assert/strict";

import { RECORDS, boot, defaultRoutes, hitsPayload, runningScan, settle } from "./harness.mjs";

/** 与后端 web_dashboard.layout_key / render.js layoutKeyOf 同一口径（\u0001 分隔）。 */
function layoutKey(record) {
  return [record.paper, record.date, record.section_no || "", record.section || "—"].join("\u0001");
}

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
        // 版本取自 harness 桩数据（哨兵值）：这里只验证「v + version」的渲染格式，不绑定真实包版本。
        assert.equal(page.text("version"), "v0.0.0-stub");
        assert.equal(rows(page.document).length, 2);

        const firstRow = rows(page.document)[0];
        assert.match(firstRow.textContent, /湛江日报/);
        assert.match(firstRow.textContent, /版内 8\/11/);
        assert.match(firstRow.textContent, /上版 70%/);
        assert.equal(firstRow.querySelector("a").getAttribute("href"), "http://example.test/zj/1");
      }),
  },
  {
    name: "命中行：版面位置排在报道右侧（行内最后一格）",
    fn: () =>
      withPage(undefined, async (page) => {
        const cells = [...rows(page.document)[0].querySelectorAll("td")];
        assert.equal(cells.length, 4);
        assert.equal(cells[0].className, "col-paper");
        assert.equal(cells[1].className, "col-date");
        assert.equal(cells[2].className, "title");
        assert.equal(cells[3].className, "col-section");
        assert.match(cells[3].textContent, /第2版：本地/);
        assert.match(cells[3].textContent, /版内 8\/11/);
        assert.doesNotMatch(cells[2].textContent, /版内 8\/11/, "报道格不应再夹带版面位置");
      }),
  },
  {
    name: "版面位置图：优先读后端按版面去重的 layouts 表",
    fn: () => {
      const record = RECORDS[0];
      const layout = {
        basis: "area",
        aspect_ratio: 0.72,
        items: [
          { url: record.url, left: 0.1, top: 0.2, right: 0.5, bottom: 0.4 },
          { url: "http://example.test/zj/other", left: 0.1, top: 0.5, right: 0.5, bottom: 0.7 },
        ],
      };
      const routes = (path, init) => {
        if (path.startsWith("/api/hits")) {
          return { body: { ...hitsPayload([record]), layouts: { [layoutKey(record)]: layout } } };
        }
        return defaultRoutes(path, init);
      };
      return withPage({ routes }, async (page) => {
        const doc = page.document;
        assert.equal(doc.querySelectorAll("#layouts .layout-block").length, 2, "应画出共享 layout 的两个格子");
        assert.equal(doc.querySelectorAll("#layouts .layout-block.hit").length, 1, "命中格高亮");
        assert.match(page.text("layouts-summary"), /1 个可绘制版面/);
      });
    },
  },
  {
    name: "筛选面板可收起：按钮文案、结果区布局与记忆同步",
    fn: () =>
      withPage(undefined, async (page) => {
        const doc = page.document;
        const panel = doc.getElementById("filter-panel");
        const toggle = doc.getElementById("filter-toggle");
        const workspace = doc.querySelector(".results-workspace");
        assert.equal(panel.hidden, false);
        assert.equal(toggle.textContent, "隐藏筛选");
        assert.equal(toggle.getAttribute("aria-expanded"), "true");

        toggle.click();
        await settle();
        assert.equal(panel.hidden, true, "收起后面板隐藏");
        assert.equal(toggle.textContent, "显示筛选");
        assert.equal(toggle.getAttribute("aria-expanded"), "false");
        assert.ok(workspace.classList.contains("is-collapsed"), "收起后结果表占满整行");
        assert.equal(page.window.localStorage.getItem("newspaper-auto.filtersOpen"), "0");

        toggle.click();
        await settle();
        assert.equal(panel.hidden, false);
        assert.ok(!workspace.classList.contains("is-collapsed"));
        assert.equal(page.window.localStorage.getItem("newspaper-auto.filtersOpen"), "1");
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
  {
    name: "图形看板：报社/关键词排名由数据渲染，零 innerHTML",
    fn: () =>
      withPage(undefined, async (page) => {
        const doc = page.document;
        const paperRows = [...doc.querySelectorAll("#paper-rank .rank-row")];
        assert.equal(paperRows.length, 2, "两家报社各一行");
        assert.ok(paperRows.some((r) => r.textContent.includes("湛江日报")));
        assert.ok(paperRows.every((r) => r.querySelector(".rank-bar")), "每行都有仪表条");
        assert.ok(paperRows.every((r) => r.getAttribute("aria-label")));

        const kwRows = [...doc.querySelectorAll("#keyword-rank .rank-row")];
        assert.equal(kwRows.length, 2);
        assert.ok(kwRows.some((r) => r.textContent.includes("人工智能")));
      }),
  },
  {
    name: "关键词排名点击后仅保留该关键词",
    fn: () =>
      withPage(undefined, async (page) => {
        const btn = [...page.document.querySelectorAll("#keyword-rank .rank-row-button")]
          .find((b) => b.textContent.includes("人工智能"));
        assert.ok(btn, "应有「人工智能」排名项");
        btn.click();
        await settle();
        assert.equal(rows(page.document).length, 1);
        assert.match(rows(page.document)[0].textContent, /光明日报/);
      }),
  },
  {
    name: "热力图：每格有 aria-label，点击可按报社×日期筛选",
    fn: () =>
      withPage(undefined, async (page) => {
        const doc = page.document;
        const cells = [...doc.querySelectorAll("#heatmap .heatmap-cell")];
        assert.ok(cells.length >= 4, "2 报社 × 2 日期 应至少有 4 格");
        assert.ok(cells.every((c) => c.getAttribute("aria-label")));

        const active = cells.find((c) => !c.disabled && Number(c.textContent) > 0);
        assert.ok(active, "应存在非零可点击格");
        active.click();
        await settle();
        assert.equal(rows(doc).length, 1, "单元格筛选后只剩该报社该日期的命中");
        assert.ok(doc.getElementById("result-context").hidden === false);
      }),
  },
  {
    name: "扫描历史面板：消费 /api/scan/history 并渲染任务摘要",
    fn: () => {
      const routes = (path, init) => {
        if ((init?.method || "GET").toUpperCase() === "GET" && path === "/api/scan/history") {
          return {
            body: {
              tasks: [
                {
                  task_id: "h1",
                  status: "done",
                  started_at: "2026-09-24 09:00:00",
                  finished_at: "2026-09-24 09:02:00",
                  duration_seconds: 120,
                  exit_code: 0,
                  keywords: ["人工智能"],
                  only: "光明日报",
                  summary: { hits: 3, new_hits: 1 },
                  error: null,
                },
              ],
            },
          };
        }
        return defaultRoutes(path, init);
      };
      return withPage({ routes }, async (page) => {
        const box = page.document.getElementById("scan-history");
        assert.match(box.textContent, /命中 3 篇/);
        assert.match(box.textContent, /人工智能/);
        assert.match(box.textContent, /光明日报/);
      });
    },
  },
  {
    name: "扫描历史面板空态带 role=status",
    fn: () =>
      withPage(undefined, async (page) => {
        const empty = page.document.querySelector("#scan-history [role=status]");
        assert.ok(empty, "空历史应有 role=status 提示");
      }),
  },
  {
    name: "头部健康度来自 /api/health（替换硬编码「本地运行」）",
    fn: () =>
      withPage(undefined, async (page) => {
        assert.match(page.text("health-text"), /服务正常/);
        assert.match(page.text("health-text"), /0\.0\.0-stub/);
        assert.equal(page.document.getElementById("health").getAttribute("data-state"), "ok");
      }),
  },
  {
    name: "服务断开时显示「重新连接」入口，恢复后一键重连",
    fn: () => {
      let down = true;
      const routes = (path, init) => (down ? { networkError: true } : defaultRoutes(path, init));
      return withPage({ routes }, async (page) => {
        const doc = page.document;
        // 首屏就断网：头部切离线，活动面板出现自助恢复入口
        assert.equal(doc.getElementById("health").getAttribute("data-state"), "off");
        const tip = doc.getElementById("service-tip");
        assert.equal(tip.hidden, false, "断网时应显示重连提示");
        assert.match(tip.textContent, /NewspaperAuto\.exe/);
        assert.match(tip.textContent, /重新连接看板/);

        // 服务恢复后点按钮
        down = false;
        doc.getElementById("service-retry").click();
        await page.flush(6);
        await settle();
        assert.equal(doc.getElementById("health").getAttribute("data-state"), "ok");
        assert.equal(tip.hidden, true, "恢复后提示应隐藏");
        assert.equal(rows(doc).length, 2, "重连后应把看板数据重新拉回来");
      });
    },
  },
  {
    name: "扫描时服务断开：状态行给出明确指引而不是只报错",
    fn: () => {
      let down = false;
      const routes = (path, init) => {
        if ((init?.method || "GET").toUpperCase() === "POST" && path === "/api/scan" && down) {
          return { networkError: true };
        }
        return defaultRoutes(path, init);
      };
      return withPage({ routes }, async (page) => {
        down = true;
        page.document
          .getElementById("scan-form")
          .dispatchEvent(new page.window.Event("submit", { bubbles: true, cancelable: true }));
        await page.flush(6);
        await settle();
        assert.match(page.text("scan-status"), /看板服务已停止/);
        assert.equal(page.document.getElementById("service-tip").hidden, false);
      });
    },
  },
  {
    name: "扫描报社 chip 的 aria-pressed 由 state 推导（dispatch 后重建）",
    fn: () =>
      withPage(undefined, async (page) => {
        const find = (name) =>
          [...page.document.querySelectorAll("#scan-papers .chip")].find((c) => c.textContent.includes(name));
        assert.equal(find("光明日报").getAttribute("aria-pressed"), "true");
        find("光明日报").click();
        await settle();
        assert.equal(find("光明日报").getAttribute("aria-pressed"), "false");
      }),
  },
];
