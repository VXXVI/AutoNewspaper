/* jsdom 测试骨架：把真实的 index.html + api.js + app.js 装进一个假浏览器环境。
 *
 * 设计要点：
 * - app.js 是 ES Module，jsdom 不执行 <script type="module">；改为把 jsdom 的 window/document
 *   暴露到 Node 全局，再用带缓存失效参数的 dynamic import 运行 app.js。
 * - api.js 是 classic IIFE，用 window.eval 在 jsdom 里执行，从而设置 window.NewspaperApi。
 * - fetch 由测试注入（默认桩返回看板所需的最小数据），**绝不联网**。
 */
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const ROOT = new URL("../../", import.meta.url);
const STATIC = new URL("src/newspaper_auto/static/", ROOT);

function staticFile(name) {
  return readFileSync(fileURLToPath(new URL(name, STATIC)), "utf8");
}

let bootSeq = 0;

export const RECORDS = [
  {
    paper: "湛江日报",
    date: "2026-09-23",
    section: "第2版：本地",
    section_no: "第02版",
    title: "中秋非遗活动进校园",
    url: "http://example.test/zj/1",
    text_len: 120,
    snippets: ["…示例大学…"],
    matched: ["示例大学"],
    geometry: { index: 8, total: 11, top: 0.7, basis: "order" },
  },
  {
    paper: "光明日报",
    date: "2026-09-22",
    section: "第1版：要闻",
    section_no: "第01版",
    title: "人工智能赋能教育",
    url: "http://example.test/gm/2",
    text_len: 90,
    snippets: ["…人工智能…"],
    matched: ["人工智能"],
  },
];

export function hitsPayload(records = RECORDS) {
  const dateCounts = {};
  const paperCounts = {};
  for (const record of records) {
    dateCounts[record.date] = (dateCounts[record.date] || 0) + 1;
    paperCounts[record.paper] = (paperCounts[record.paper] || 0) + 1;
  }
  return {
    generated_at: "2026-09-24 10:00:00",
    // 桩数据里的版本号：与真实包版本无关，用哨兵值以免被误读成「版本漂移漏检」。
    version: "0.0.0-stub",
    data_dir: "/tmp/data",
    archive_file: "/tmp/data/hits/archive.json",
    count: records.length,
    papers: Object.keys(paperCounts).sort(),
    paper_counts: paperCounts,
    keywords: Array.from(new Set(records.flatMap((r) => r.matched || []))).sort(),
    seen_counts: { 示例大学: 3 },
    date_counts: dateCounts,
    records,
    has_web_config: true,
    default_keywords: ["人工智能"],
    // 看板首屏会聚焦「已配置关键词」；样例两条命中分属两个关键词，故都配置。
    configured_keywords: ["示例大学", "人工智能"],
  };
}

export function papersPayload() {
  const config = { name: "光明日报", style: "ymd", tpl: "https://example.test/{a}/node_01.html" };
  return {
    builtin: ["光明日报", "南方日报"],
    custom: [],
    disabled: [],
    removed: [],
    effective: ["光明日报", "南方日报"],
    items: [
      { name: "光明日报", source: "builtin", enabled: true, config },
      { name: "南方日报", source: "builtin", enabled: true, config: { ...config, name: "南方日报" } },
    ],
  };
}

export function configPayload(overrides = {}) {
  return {
    keywords: ["示例大学"],
    days: 2,
    workers: 8,
    mode: "days",
    since: null,
    until: null,
    only: "",
    ...overrides,
  };
}

export function idleScan() {
  return {
    status: "idle",
    task_id: null,
    params: {},
    started_at: null,
    finished_at: null,
    duration_seconds: null,
    log: [],
    log_cursor: 0,
    logs_truncated: false,
    progress: { completed: 0, total: 0, percent: null, current: "" },
    summary: null,
    exit_code: null,
    error: null,
    cancel_requested: false,
  };
}

export function runningScan(overrides = {}) {
  return {
    ...idleScan(),
    status: "running",
    task_id: "task-1",
    started_at: "2026-09-24 10:00:00",
    params: { keywords: ["示例大学"] },
    progress: { completed: 0, total: 2, percent: 0, current: "" },
    ...overrides,
  };
}

function makeResponse({ status = 200, body = {} } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(typeof body === "string" ? body : JSON.stringify(body)),
  };
}

/** 默认接口桩：看板初始化所需的四个 GET。 */
export function defaultRoutes(path, init) {
  const method = (init?.method || "GET").toUpperCase();
  if (method === "GET") {
    if (path.startsWith("/api/hits")) return { body: hitsPayload() };
    if (path === "/api/health") return { body: { ok: true, version: "0.0.0-stub" } };
    if (path === "/api/config") return { body: configPayload() };
    if (path === "/api/papers") return { body: papersPayload() };
    if (path.startsWith("/api/scan/history")) return { body: { tasks: [] } };
    if (path.startsWith("/api/scan")) return { body: idleScan() };
  }
  return { status: 404, body: { error: "未桩接的接口 " + method + " " + path } };
}

export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function flush(rounds = 10) {
  for (let i = 0; i < rounds; i += 1) {
    await sleep(0);
  }
}

/** 越过 app.js 里 140ms 的渲染防抖。 */
export async function settle() {
  await sleep(200);
}

export async function boot(options = {}) {
  // 不用 pretendToBeVisual：它会开一个永不停止的 rAF 循环，让 node --test 结束后仍挂起。
  const dom = new JSDOM(staticFile("index.html"), {
    url: "http://127.0.0.1:8788/",
    runScripts: "outside-only",
  });
  const { window } = dom;

  const calls = [];
  const routes = options.routes || defaultRoutes;
  const fetchMock = (path, init = {}) => {
    const method = (init.method || "GET").toUpperCase();
    let body = null;
    if (typeof init.body === "string") {
      try { body = JSON.parse(init.body); } catch { body = init.body; }
    }
    calls.push({ path: String(path), method, body });
    const route = routes(String(path), init);
    // 模拟「连接被拒」：真实浏览器里 fetch 抛的是本域 TypeError（api.js 据此判定服务离线）。
    // 必须用 window.TypeError，否则跨 realm 的 instanceof 判定会失败。
    if (route && route.networkError) {
      return Promise.reject(new window.TypeError("Failed to fetch"));
    }
    return Promise.resolve(makeResponse(route));
  };
  window.fetch = fetchMock;

  // 让 app.js（Node ESM）能取到 jsdom 的 DOM
  globalThis.window = window;
  globalThis.document = window.document;
  globalThis.fetch = fetchMock;
  globalThis.confirm = window.confirm = () => true;
  if (!window.URL.createObjectURL) window.URL.createObjectURL = () => "blob:stub";
  if (!window.URL.revokeObjectURL) window.URL.revokeObjectURL = () => {};
  if (options.EventSource) window.EventSource = options.EventSource;

  // api.js（classic IIFE）→ window.NewspaperApi
  window.eval(staticFile("api.js"));

  const appUrl = new URL("src/newspaper_auto/static/app.js", ROOT).href + `?boot=${++bootSeq}`;
  await import(appUrl);
  await flush();
  // app 现在统一经 store.subscribe 防抖渲染（140ms）；等首屏渲染落地后再断言。
  await settle();

  return {
    dom,
    window,
    document: window.document,
    calls,
    flush,
    text: (id) => window.document.getElementById(id)?.textContent ?? null,
    close: () => {
      try { window.close(); } catch { /* ignore */ }
      // jsdom 会留下未 close 的 socket 句柄（node --test 会因此挂到超时）；
      // 这里显式销毁，保证测试进程能立即退出。仅影响测试环境。
      for (const handle of process._getActiveHandles()) {
        const name = handle && handle.constructor && handle.constructor.name;
        if (name === "Socket" && !handle.destroyed) {
          try { handle.destroy(); } catch { /* ignore */ }
        }
      }
    },
  };
}
