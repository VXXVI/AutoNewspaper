/* 数字报关键词监测 · 本地看板（ES Module 入口）
 *
 * 纯原生 JS：无框架、无构建、无 CDN。所有来自抓取的数据（含扫描日志）一律用
 * textContent / createElement 渲染，绝不拼 innerHTML，避免标题/正文/日志里的
 * 内容被当作 HTML 执行。纯函数工具见 ./format.js。
 */
import {
  ISO_DATE_RE,
  csvCell,
  geomText,
  parseInt10,
  shiftDate,
  spanDays,
  splitKeywords,
  todayIso,
} from "./format.js";

(function () {
  "use strict";

  var INITIAL_ROWS = 50;
  var ROW_STEP = 50;
  var MAX_LAYOUTS = 24;
  var SCAN_POLL_MS = 1200;
  var api = window.NewspaperApi;

  var scanTimer = null;
  var scanStream = null;
  var scanPollInFlight = false;
  var scanLogCursor = 0;
  var scanLogLines = [];
  var currentScanTaskId = null;
  var renderTimer = null;
  var exportBusy = false;
  var editingPaperName = null;
  var loadRequestId = 0;

  // 「只扫选中报社」：scanPaperNames = 可选报社（GET /api/papers 的 effective）；
  // scanPaperSelected = null 表示尚未筛选（全选），否则为选中名字集合。
  var scanPaperNames = [];
  var scanPaperSelected = null;
  var pendingOnly = null;      // 从 /api/config 读到的 only，等报社列表到位后再应用
  var scanRunning = false;

  var PAPERS_COMMIT_CMD =
    'git fetch origin main && git pull --rebase --autostash origin main && git add papers.custom.json && git commit -m "chore(papers): 更新报社配置" && git push && git ls-remote origin refs/heads/main';

  var state = {
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

  function $(id) { return document.getElementById(id); }

  // 单向数据流：用户动作 → dispatch(patch) → state → render。
  // 事件处理器只描述「状态怎么变」，不再各自调用 render()；
  // 默认走 140ms 防抖渲染，需要同步生效时传 { immediate: true }。
  function dispatch(patch, options) {
    Object.assign(state, patch);
    if (options && options.silent) return;      // 由调用方自行 render（如数据加载）
    if (options && options.immediate) render();
    else scheduleRender();
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  // 只允许 http/https，杜绝 javascript: 等危险协议
  function safeUrl(raw) {
    try {
      var u = new URL(raw, window.location.origin);
      return (u.protocol === "http:" || u.protocol === "https:") ? u.href : null;
    } catch (err) {
      return null;
    }
  }

  /* -------------------------------------------------------------- 数据 */
  function load(options) {
    options = options || {};
    var requestId = ++loadRequestId;
    $("meta").textContent = "加载中…";
    var hadData = Boolean(state.data);
    var allPapersSelected = hadData && state.papers.size === state.data.papers.length;
    var allKeywordsSelected = hadData && state.keywords.size === state.data.keywords.length;
    api.get("/api/hits")
      .then(function (data) {
        if (requestId !== loadRequestId) return;
        var requestedFocus = Array.isArray(options.focusKeywords) && options.focusKeywords.length
          ? options.focusKeywords
          : (!hadData && Array.isArray(data.configured_keywords) && data.configured_keywords.length
            ? data.configured_keywords : null);
        var patch = { data: data };
        if (!hadData || !options.preserve || requestedFocus) {
          patch.papers = new Set(data.papers);
          patch.focusKeywords = requestedFocus ? requestedFocus.slice() : null;
          patch.keywords = new Set(requestedFocus || data.keywords);
          patch.linkedFilter = null;
          patch.q = "";
          patch.from = "";
          patch.to = "";
          patch.sort = "date-desc";
          patch.preset = "all";
          patch.visibleRows = INITIAL_ROWS;
          dispatch(patch, { silent: true });
          $("q").value = "";
          $("from").value = "";
          $("to").value = "";
          $("sort").value = "date-desc";
        } else {
          patch.papers = allPapersSelected
            ? new Set(data.papers)
            : new Set(data.papers.filter(function (name) { return state.papers.has(name); }));
          patch.keywords = state.focusKeywords
            ? new Set(state.focusKeywords)
            : (allKeywordsSelected
              ? new Set(data.keywords)
              : new Set(data.keywords.filter(function (name) { return state.keywords.has(name); })));
          dispatch(patch, { silent: true });
        }
        $("version").textContent = "v" + data.version;
        buildControls();
        updatePresetUI();
        render();
      })
      .catch(function (err) {
        if (requestId !== loadRequestId) return;
        $("meta").textContent = "加载失败：" + err.message;
      });
  }

  function buildControls() {
    var data = state.data;

    var paperBox = $("paper-chips");
    paperBox.textContent = "";
    data.papers.forEach(function (name) {
      paperBox.appendChild(makeChip(name, state.papers.has(name), function (on) {
        var next = new Set(state.papers);
        if (on) next.add(name); else next.delete(name);
        dispatch({ papers: next });
      }));
    });

    var kwBox = $("keyword-chips");
    kwBox.textContent = "";
    var keywordNames = data.keywords.slice();
    (state.focusKeywords || []).forEach(function (kw) {
      if (keywordNames.indexOf(kw) === -1) keywordNames.unshift(kw);
    });
    keywordNames.forEach(function (kw) {
      kwBox.appendChild(makeChip(kw, state.keywords.has(kw), function (on) {
        var next = new Set(state.keywords);
        if (on) next.add(kw); else next.delete(kw);
        dispatch({ keywords: next, focusKeywords: null });
      }));
    });

    var dates = Object.keys(data.date_counts).sort();
    ["from", "to"].forEach(function (id) {
      var input = $(id);
      input.min = dates.length ? dates[0] : "";
      input.max = dates.length ? dates[dates.length - 1] : "";
    });
  }

  function makeChip(label, initialOn, toggle) {
    var chip = el("button", initialOn ? "chip on" : "chip", label);
    chip.type = "button";
    chip.setAttribute("aria-pressed", initialOn ? "true" : "false");
    chip.addEventListener("click", function () {
      var on = !chip.classList.contains("on");
      chip.classList.toggle("on", on);
      chip.setAttribute("aria-pressed", on ? "true" : "false");
      toggle(on);
      dispatch({ linkedFilter: null, visibleRows: INITIAL_ROWS }, { immediate: true });
    });
    return chip;
  }

  /* -------------------------------------------------------------- 过滤 */
  function filtered() {
    if (!state.data) return [];
    var q = state.q.trim().toLowerCase();
    return state.data.records.filter(function (r) {
      if (!state.papers.has(r.paper)) return false;
      if (!state.keywords.size || !r.matched.some(function (k) { return state.keywords.has(k); })) return false;
      if (state.from && (!r.date || r.date < state.from)) return false;
      if (state.to && (!r.date || r.date > state.to)) return false;
      if (q) {
        var hay = (r.title + " " + r.section + " " + r.snippets.join(" ")).toLowerCase();
        if (hay.indexOf(q) === -1) return false;
      }
      if (state.linkedFilter) {
        if (state.linkedFilter.type === "date" && r.date !== state.linkedFilter.date) return false;
        if (state.linkedFilter.type === "layout" && !state.linkedFilter.urls.has(r.url)) return false;
      }
      return true;
    });
  }

  function byDateDesc(a, b) {
    if (a.date !== b.date) return a.date < b.date ? 1 : -1;
    return a.url < b.url ? 1 : (a.url > b.url ? -1 : 0);
  }

  function sortRecords(list) {
    var copy = list.slice();
    var cmp = {
      "date-desc": byDateDesc,
      "date-asc": function (a, b) { return -byDateDesc(a, b); },
      "paper": function (a, b) { return a.paper.localeCompare(b.paper, "zh") || byDateDesc(a, b); },
      "title": function (a, b) { return a.title.localeCompare(b.title, "zh"); }
    }[state.sort] || byDateDesc;
    copy.sort(cmp);
    return copy;
  }

  function clearLinkedFilter(renderNow) {
    dispatch({ linkedFilter: null, visibleRows: INITIAL_ROWS }, { immediate: renderNow !== false });
  }

  function showLinkedResults(filter) {
    var current = state.linkedFilter;
    var same = current && current.type === filter.type && current.key === filter.key;
    dispatch({ linkedFilter: same ? null : filter, visibleRows: INITIAL_ROWS }, { immediate: true });
    $("results-card").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function showAllHistory() {
    if (!state.data) return;
    dispatch({ focusKeywords: null, keywords: new Set(state.data.keywords), linkedFilter: null, visibleRows: INITIAL_ROWS });
    buildControls();  // 防抖渲染前重建 chips
  }

  /* -------------------------------------------------------------- 日期快捷区间 */
  function dateBounds() {
    var ds = Object.keys(state.data.date_counts).sort();
    return ds.length ? [ds[0], ds[ds.length - 1]] : ["", ""];
  }

  function updatePresetUI() {
    var box = $("preset-chips");
    if (!box) return;
    Array.prototype.forEach.call(box.querySelectorAll("[data-preset]"), function (btn) {
      var active = btn.getAttribute("data-preset") === state.preset;
      btn.classList.toggle("on", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  // 以「数据里最新的日期」为基准，避免用今天算导致历史数据筛不出东西
  function applyPreset(kind) {
    var patch = { linkedFilter: null, preset: kind, visibleRows: INITIAL_ROWS };
    if (state.data) {
      var latest = dateBounds()[1];
      if (kind === "all") { patch.from = ""; patch.to = ""; }
      else if (latest && kind === "today") { patch.from = latest; patch.to = latest; }
      else if (latest && kind === "7d") { patch.from = shiftDate(latest, -6); patch.to = latest; }
      else if (latest && kind === "30d") { patch.from = shiftDate(latest, -29); patch.to = latest; }
    }
    dispatch(patch);
    $("from").value = state.from;
    $("to").value = state.to;
    updatePresetUI();
  }

  /* -------------------------------------------------------------- 导出结果 */
  // 浏览器下载兜底：不依赖后端写文件能力，始终导出当前筛选的 CSV。
  function downloadCsv() {
    if (!state.data) return;
    var rows = sortRecords(filtered());
    var head = ["报社", "日期", "版面", "标题", "链接", "命中关键词", "上下文"];
    var lines = [head.map(csvCell).join(",")];
    rows.forEach(function (r) {
      lines.push([
        r.paper, r.date, r.section || "", r.title, r.url,
        r.matched.filter(function (keyword) { return state.keywords.has(keyword); }).join(";"),
        r.snippets.slice(0, 2).join(" ")
      ].map(csvCell).join(","));
    });
    var blob = new Blob(["\ufeff" + lines.join("\r\n") + "\r\n"], { type: "text/csv;charset=utf-8" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "newspaper-hits-" + new Date().toISOString().slice(0, 10) + ".csv";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(a.href);
  }

  function defaultExportDirectory() {
    var base = state.data && typeof state.data.data_dir === "string" ? state.data.data_dir.trim() : "";
    if (!base) return "exports";
    var separator = base.indexOf("\\") !== -1 && base.indexOf("/") === -1 ? "\\" : "/";
    return base.replace(/[\\/]+$/, "") + separator + "exports";
  }

  function exportFilters() {
    var linkedDate = state.linkedFilter && state.linkedFilter.type === "date"
      ? state.linkedFilter.date : null;
    var linkedUrls = state.linkedFilter && state.linkedFilter.type === "layout"
      ? Array.from(state.linkedFilter.urls) : null;
    return {
      papers: Array.from(state.papers),
      keywords: Array.from(state.keywords),
      urls: linkedUrls,
      from: linkedDate || state.from,
      to: linkedDate || state.to,
      query: state.q.trim(),
      sort: state.sort
    };
  }

  function setExportStatus(text, tone) {
    var status = $("export-status");
    status.textContent = text || "";
    if (tone) status.setAttribute("data-state", tone);
    else status.removeAttribute("data-state");
  }

  function updateExportPathPreview(path, isFile) {
    $("export-path-label").textContent = isFile ? "已保存文件" : "目标文件夹";
    $("export-path-preview").textContent = path || "请填写保存文件夹";
  }

  function updateExportScope() {
    var rows = state.data ? filtered() : [];
    var scope = $("export-scope");
    var title = scope.querySelector("b");
    var detail = scope.querySelector("span");
    title.textContent = rows.length ? "将导出当前筛选的 " + rows.length + " 条报道" : "当前筛选没有可导出的报道";
    detail.textContent = rows.length
      ? "服务端会重新应用相同筛选，并返回实际保存路径与条数。"
      : "请关闭窗口并调整筛选条件后再试。";
    $("export-save").disabled = exportBusy || rows.length === 0;
    $("export-browser").disabled = exportBusy || rows.length === 0;
  }

  function setExportBusy(busy) {
    exportBusy = busy;
    var save = $("export-save");
    save.textContent = busy ? "正在保存…" : "保存到文件夹";
    $("export-format").disabled = busy;
    $("export-directory").disabled = busy;
    updateExportScope();
  }

  function openExportDialog() {
    if (!state.data) return;
    var directory = $("export-directory");
    if (!directory.value.trim()) directory.value = defaultExportDirectory();
    updateExportPathPreview(directory.value.trim());
    setExportStatus("");
    updateExportScope();
    var dialog = $("export-dialog");
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
    window.setTimeout(function () { directory.focus(); directory.select(); }, 0);
  }

  function closeExportDialog() {
    if (exportBusy) return;
    var dialog = $("export-dialog");
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  }

  function saveExport(event) {
    event.preventDefault();
    if (exportBusy || !state.data) return;
    var directory = $("export-directory").value.trim();
    if (!directory) {
      setExportStatus("请填写保存文件夹的完整路径。", "error");
      $("export-directory").focus();
      return;
    }

    setExportBusy(true);
    setExportStatus("正在整理当前筛选结果并写入文件…", "busy");
    api.post("/api/export", {
      directory: directory,
      format: $("export-format").value,
      filters: exportFilters()
    }, { timeout: 60000 })
      .then(function (result) {
        var count = typeof result.count === "number" ? result.count : 0;
        var format = typeof result.format === "string" ? result.format.toUpperCase() : "文件";
        var path = typeof result.path === "string" ? result.path : directory;
        updateExportPathPreview(path, true);
        setExportStatus("导出成功：已保存 " + count + " 条 " + format + " 记录。", "success");
      })
      .catch(function (err) {
        var unavailable = err.status === 0 || err.status === 404 || err.status === 405 || err.status === 501;
        setExportStatus(unavailable
          ? "当前服务暂不支持写入指定文件夹。可使用“浏览器下载 CSV”保存当前筛选结果。"
          : "导出失败：" + err.message, "error");
      })
      .finally(function () { setExportBusy(false); });
  }

  function downloadExportFallback() {
    if (!state.data || !filtered().length) return;
    downloadCsv();
    setExportStatus("已交给浏览器下载当前筛选的 CSV；保存位置由浏览器的下载设置决定。", "success");
  }

  /* -------------------------------------------------------------- 扫描设置 */

  function setScanStatus(text, tone) {
    var status = $("scan-status");
    status.textContent = text;
    if (!tone) {
      if (/失败|未完整|错误|至少|不能|最多|请选择|晚于|超过/.test(text)) tone = "error";
      else if (/完成/.test(text)) tone = "success";
      else if (/正在|扫描中|已启动|接入进度/.test(text)) tone = "running";
      else tone = "idle";
    }
    status.setAttribute("data-state", tone);
  }

  function setScanRunning(running) {
    scanRunning = running;
    $("scan-card").setAttribute("aria-busy", running ? "true" : "false");
    var cancelBtn = $("scan-cancel");
    if (cancelBtn) {
      cancelBtn.hidden = !running;
      cancelBtn.disabled = false;
    }
    updateScanButton();
  }

  // 当前选中的扫描报社（scanPaperSelected 为 null 时视为全选）
  function scanSelectedNames() {
    if (!scanPaperSelected) return scanPaperNames.slice();
    return scanPaperNames.filter(function (name) { return scanPaperSelected.has(name); });
  }

  function updateScanPapersHint() {
    var hint = $("scan-papers-hint");
    if (!hint) return;
    if (!scanPaperNames.length) { hint.textContent = "⚠ 没有已启用的报社，请先在下方恢复或添加"; return; }
    var n = scanSelectedNames().length;
    hint.textContent = n === 0
      ? "⚠ 至少选择一家报社才能扫描"
      : "已选 " + n + " / " + scanPaperNames.length + " 家";
  }

  function updateScanButton() {
    var btn = $("scan-run");
    if (!btn) return;
    var zero = scanPaperNames.length === 0 || scanSelectedNames().length === 0;
    btn.disabled = scanRunning || zero;
    var label = btn.querySelector("span:last-child");
    if (label) label.textContent = scanRunning ? "扫描中…" : "开始扫描";
    else btn.textContent = scanRunning ? "扫描中…" : "开始扫描";
    btn.classList.toggle("is-running", scanRunning);
  }

  // 渲染扫描用的报社多选 chips（默认全选；全部用 createElement/textContent）
  function renderScanPapers() {
    var box = $("scan-papers");
    if (!box) return;
    box.textContent = "";
    scanPaperNames.forEach(function (name) {
      var on = !scanPaperSelected || scanPaperSelected.has(name);
      var chip = el("button", on ? "chip on" : "chip", name);
      chip.type = "button";
      chip.setAttribute("aria-pressed", on ? "true" : "false");
      chip.addEventListener("click", function () {
        if (!scanPaperSelected) scanPaperSelected = new Set(scanPaperNames);
        if (scanPaperSelected.has(name)) scanPaperSelected.delete(name);
        else scanPaperSelected.add(name);
        var nowOn = scanPaperSelected.has(name);
        chip.classList.toggle("on", nowOn);
        chip.setAttribute("aria-pressed", nowOn ? "true" : "false");
        updateScanPapersHint();
        updateScanButton();
      });
      box.appendChild(chip);
    });
    updateScanPapersHint();
    updateScanButton();
  }

  // 把配置里的 only（逗号分隔字符串）应用到 chip 选择；交集为空则回落到全选
  function applyConfigOnly(onlyText) {
    var wanted = splitKeywords(onlyText || "");
    if (!wanted.length) { scanPaperSelected = null; return; }
    var selected = new Set();
    wanted.forEach(function (name) {
      if (scanPaperNames.indexOf(name) !== -1) selected.add(name);
    });
    // 有显式选择但交集为空时必须保留空集合并禁用扫描，绝不静默扩大为“全选”。
    scanPaperSelected = selected;
  }

  // 切换「按天数 / 按日期范围」时显示/隐藏对应输入
  function updateScanModeUI() {
    var range = $("scan-mode").value === "range";
    $("scan-days-field").hidden = range;
    $("scan-since-field").hidden = !range;
    $("scan-until-field").hidden = !range;
  }

  // 日期范围默认值：until = 今天，since = 今天减 1 天（对齐 DEFAULT_DAYS=2）
  function ensureRangeDefaults() {
    var today = todayIso();
    if (!$("scan-since").value && ISO_DATE_RE.test($("scan-until").value)) {
      $("scan-since").value = shiftDate($("scan-until").value, -1);
    } else if (!$("scan-since").value) {
      $("scan-since").value = shiftDate(today, -1);
    }
    if (!$("scan-until").value) $("scan-until").value = today;
  }

  // 启动时回填上次配置；失败就用默认值，不阻塞页面
  function loadScanConfig() {
    api.get("/api/config")
      .then(function (cfg) {
        if (Array.isArray(cfg.keywords) && cfg.keywords.length) {
          $("scan-kw").value = cfg.keywords.join(", ");
        }
        if (typeof cfg.days === "number") $("scan-days").value = cfg.days;
        if (typeof cfg.workers === "number") $("scan-workers").value = cfg.workers;
        $("scan-mode").value = cfg.mode === "range" ? "range" : "days";
        if (typeof cfg.since === "string" && ISO_DATE_RE.test(cfg.since)) {
          $("scan-since").value = cfg.since;
        }
        if (typeof cfg.until === "string" && ISO_DATE_RE.test(cfg.until)) {
          $("scan-until").value = cfg.until;
        }
        pendingOnly = typeof cfg.only === "string" ? cfg.only : "";
        if (scanPaperNames.length) {
          applyConfigOnly(pendingOnly);
          pendingOnly = null;
          renderScanPapers();
        }
        ensureRangeDefaults();
        updateScanModeUI();
      })
      .catch(function () {
        pendingOnly = "";
        ensureRangeDefaults();
        updateScanModeUI();
      });
  }

  function buildScanBody() {
    var keywords = splitKeywords($("scan-kw").value);
    var workers = parseInt10($("scan-workers").value);

    // 与后端一致的基础校验：不合法就地提示，不发请求
    if (!keywords.length) return { error: "请至少填写一个关键词" };
    if (keywords.length > 10) return { error: "关键词最多 10 个" };
    if (keywords.some(function (k) { return k.length > 50; })) {
      return { error: "单个关键词不能超过 50 个字符" };
    }
    if (isNaN(workers) || workers < 1 || workers > 16) {
      return { error: "并发需为 1 ~ 16 之间的整数" };
    }

    // 「只扫选中报社」：全选时也显式发送 only，不靠「空 = 全部」的隐含语义；
    // 前端不允许提交 0 家（后端对零匹配会以退出码 2 拒绝）。
    var selectedPapers = scanSelectedNames();
    if (scanPaperNames.length && selectedPapers.length === 0) {
      return { error: "请至少选择一家报社" };
    }
    var only = selectedPapers.join(",");

    if ($("scan-mode").value === "range") {
      var since = $("scan-since").value;
      var until = $("scan-until").value;
      if (!ISO_DATE_RE.test(since) || !ISO_DATE_RE.test(until)) {
        return { error: "请选择合法的起始与结束日期" };
      }
      if (since > until) return { error: "起始日期不能晚于结束日期" };
      if (spanDays(since, until) > 62) return { error: "日期跨度不能超过 62 天" };
      return {
        body: { keywords: keywords, workers: workers, mode: "range", since: since, until: until, only: only }
      };
    }

    var days = parseInt10($("scan-days").value);
    if (isNaN(days) || days < 1 || days > 62) {
      return { error: "天数需为 1 ~ 62 之间的整数" };
    }
    return { body: { keywords: keywords, workers: workers, mode: "days", days: days, only: only } };
  }

  function startScan() {
    var built = buildScanBody();
    if (built.error) { setScanStatus(built.error); return; }
    var body = built.body;

    // 保存配置：失败只提示，不阻断扫描
    api.post("/api/config", body).catch(function () { /* 保存失败不影响本次扫描 */ });

    stopStreaming();
    stopPolling();
    setScanRunning(true);
    setScanStatus("正在提交扫描…");
    scanLogCursor = 0;
    scanLogLines = [];
    $("scan-log").textContent = "";
    updateScanProgress(null);

    api.post("/api/scan", body)
      .then(function (task) {
        consumeScanTask(task, true);
        setScanStatus("扫描已启动");
        beginStreaming();
      })
      .catch(function (err) {
        if (err.status === 409) {
          setScanStatus("已有扫描在运行，正在接入进度…");
          resumeScan();
          return;
        }
        setScanRunning(false);
        setScanStatus("启动失败：" + err.message);
      });
  }

  // 优先用 SSE 接收进度（低延迟、少请求）；不可用或断开时回退到轮询。
  function beginStreaming() {
    if (scanStream) return;
    if (typeof window.EventSource !== "function") { beginPolling(); return; }
    var source;
    try {
      source = new window.EventSource("/api/scan/events?after=" + encodeURIComponent(scanLogCursor));
    } catch (err) {
      beginPolling();
      return;
    }
    scanStream = source;
    source.onmessage = function (event) {
      var task;
      try { task = JSON.parse(event.data); } catch (err) { return; }
      consumeScanTask(task, false);
      if (applyTaskOutcome(task)) return;
      var progress = task.progress || {};
      setScanStatus(progress.total
        ? "扫描中：" + progress.completed + " / " + progress.total
        : "扫描中…");
    };
    source.onerror = function () {
      stopStreaming();
      if (scanRunning) beginPolling();  // 流异常断开：回退轮询兜底收尾
    };
  }

  function stopStreaming() {
    if (scanStream) { scanStream.close(); scanStream = null; }
  }

  function beginPolling() {
    schedulePoll(0);
  }

  function schedulePoll(delay) {
    if (scanTimer) return;
    scanTimer = window.setTimeout(function () {
      scanTimer = null;
      pollScan();
    }, delay);
  }

  function stopPolling() {
    if (scanTimer) { window.clearTimeout(scanTimer); scanTimer = null; }
  }

  function renderScanLog() {
    var box = $("scan-log");
    box.textContent = scanLogLines.join("\n");
    box.scrollTop = box.scrollHeight;
  }

  function updateScanProgress(task) {
    var wrap = $("scan-progress");
    var bar = $("scan-progress-bar");
    var text = $("scan-progress-text");
    if (!task || task.status === "idle") {
      wrap.hidden = true;
      bar.style.width = "0%";
      text.textContent = "正在准备…";
      wrap.setAttribute("aria-valuenow", "0");
      return;
    }
    var progress = task.progress || {};
    var percent = typeof progress.percent === "number" ? progress.percent : 0;
    wrap.hidden = false;
    bar.style.width = Math.max(0, Math.min(100, percent)) + "%";
    wrap.setAttribute("aria-valuenow", String(percent));
    if (progress.total) {
      text.textContent = progress.completed + " / " + progress.total + "（" + percent + "%）";
    } else if (task.status === "running") {
      text.textContent = "正在扫描…";
    } else if (task.status === "cancelled") {
      text.textContent = "已取消";
    } else {
      text.textContent = task.status === "done" ? "扫描完成" : "扫描未完整完成";
    }
  }

  function consumeScanTask(task, replace) {
    if (task.task_id && currentScanTaskId && task.task_id !== currentScanTaskId) {
      replace = true;
      scanLogCursor = 0;
    }
    if (task.task_id) currentScanTaskId = task.task_id;
    if (replace || task.logs_truncated) scanLogLines = [];
    if (Array.isArray(task.log) && task.log.length) {
      scanLogLines = scanLogLines.concat(task.log).slice(-300);
      renderScanLog();
    }
    if (typeof task.log_cursor === "number") scanLogCursor = task.log_cursor;
    updateScanProgress(task);
  }

  // 回到终态：停掉所有推送、更新状态行、刷新结果。返回是否已是终态。
  function applyTaskOutcome(task) {
    if (task.status === "done") {
      stopPolling(); stopStreaming(); setScanRunning(false);
      var summary = task.summary;
      setScanStatus(summary && typeof summary.hits === "number"
        ? "扫描完成：命中 " + summary.hits + " 篇，新增 " + Number(summary.new_hits || 0) + " 篇"
        : "扫描完成");
      load({ preserve: true, focusKeywords: task.params && task.params.keywords });
      return true;
    }
    if (task.status === "error") {
      stopPolling(); stopStreaming(); setScanRunning(false);
      setScanStatus("扫描未完整完成：" + (task.error || "请查看运行日志"));
      $("scan-log-details").open = true;
      load({ preserve: true, focusKeywords: task.params && task.params.keywords });
      return true;
    }
    if (task.status === "cancelled") {
      stopPolling(); stopStreaming(); setScanRunning(false);
      setScanStatus("扫描已取消");
      $("scan-log-details").open = true;
      load({ preserve: true, focusKeywords: task.params && task.params.keywords });
      return true;
    }
    return false;
  }

  function pollScan() {
    if (scanPollInFlight) return;
    scanPollInFlight = true;
    api.get("/api/scan?after=" + encodeURIComponent(scanLogCursor))
      .then(function (task) {
        consumeScanTask(task, false);
        if (applyTaskOutcome(task)) return;
        var progress = task.progress || {};
        setScanStatus(progress.total
          ? "扫描中：" + progress.completed + " / " + progress.total
          : "扫描中…");
        schedulePoll(SCAN_POLL_MS);
      })
      .catch(function (err) {
        stopPolling();
        setScanRunning(false);
        setScanStatus("轮询失败：" + err.message);
      })
      .finally(function () {
        scanPollInFlight = false;
      });
  }

  function cancelScan() {
    var btn = $("scan-cancel");
    if (btn) btn.disabled = true;
    setScanStatus("正在取消…");
    api.post("/api/scan/cancel", {}).catch(function (err) {
      if (btn) btn.disabled = false;
      setScanStatus("取消失败：" + err.message);
    });
  }

  function resumeScan() {
    api.get("/api/scan")
      .then(function (task) {
        if (!task) return;
        scanLogLines = [];
        consumeScanTask(task, true);
        if (task.status === "running") {
          setScanRunning(true);
          setScanStatus("自动扫描进行中…");
          beginStreaming();
        } else if (task.status === "done") {
          setScanStatus("最近一次扫描已完成");
        } else if (task.status === "cancelled") {
          setScanStatus("最近一次扫描已取消");
        } else if (task.status === "error") {
          setScanStatus("最近一次扫描未完整完成：" + (task.error || "请查看日志"));
        }
      })
      .catch(function () { /* 首屏恢复失败不阻塞其他功能 */ });
  }

  /* -------------------------------------------------------------- 报社管理 */
  function setPapersStatus(text, tone) {
    $("papers-status").textContent = text;
    var feedback = $("papers-feedback");
    feedback.textContent = text === "—" ? "" : text;
    if (!tone) {
      if (/失败|不能|请先|至少|未找到/.test(text)) tone = "error";
      else if (/已保存|已删除|已停用|已恢复|已启用/.test(text)) tone = "success";
      else tone = "idle";
    }
    feedback.setAttribute("data-state", tone);
  }

  function setPapersHint(text) { $("papers-hint").textContent = text; }

  function renderPapers(view) {
    var box = $("papers-list");
    if (!box) return;
    box.textContent = "";

    var items = Array.isArray(view.items) ? view.items.slice() : [];

    if (!items.length) {
      box.appendChild(el("span", "hint", "暂无报社"));
      return;
    }

    items.forEach(function (item) {
      var name = item.name;
      var enabled = item.enabled !== false;
      var sourceLabels = { builtin: "内置", custom: "自定义", override: "已覆盖" };
      var source = sourceLabels[item.source] || "自定义";
      var row = el("div", enabled ? "papers-row" : "papers-row is-disabled");
      row.setAttribute("data-paper-name", name);
      row.appendChild(el("span", "paper-name", name));
      row.appendChild(el("span", "paper-src " + (item.source === "builtin" ? "src-builtin" : "src-custom"), source));
      row.appendChild(el("span", enabled ? "paper-state is-on" : "paper-state", enabled ? "已启用" : "已停用"));

      var actions = el("span", "paper-actions");
      var testBtn = el("button", "chip", "试抓");
      testBtn.type = "button";
      testBtn.setAttribute("aria-label", "试抓" + name);
      testBtn.addEventListener("click", function () {
        runPaperTest(item.config || { name: name });
      });
      actions.appendChild(testBtn);

      var editBtn = el("button", "chip", item.source === "builtin" ? "修改规则" : "编辑");
      editBtn.type = "button";
      editBtn.setAttribute("aria-label", "编辑" + name + "抓取规则");
      editBtn.addEventListener("click", function () { fillPaperForm(item.config || {}, item.source); });
      actions.appendChild(editBtn);

      var toggleBtn = el("button", enabled ? "chip danger" : "chip enable", enabled ? "停用" : "恢复");
      toggleBtn.type = "button";
      toggleBtn.setAttribute("role", "switch");
      toggleBtn.setAttribute("aria-checked", enabled ? "true" : "false");
      toggleBtn.setAttribute("aria-label", (enabled ? "停用" : "恢复") + name);
      toggleBtn.addEventListener("click", function () { setPaperEnabled(name, !enabled, row, toggleBtn); });
      actions.appendChild(toggleBtn);

      if (item.source === "custom" || item.source === "override") {
        var deleteLabel = item.source === "override" ? "恢复默认" : "删除";
        var delBtn = el("button", item.source === "override" ? "chip" : "chip danger", deleteLabel);
        delBtn.type = "button";
        delBtn.setAttribute("aria-label", deleteLabel + name);
        delBtn.addEventListener("click", function () { deletePaper(name, item.source); });
        actions.appendChild(delBtn);
      }

      row.appendChild(actions);
      box.appendChild(row);
    });

    // 存在自定义 / 覆盖 / 停用配置时，提供先同步再推送的安全 Shell 命令。
    var commitRow = $("papers-commit-row");
    if (commitRow) commitRow.hidden = !((view.custom && view.custom.length) || (view.disabled && view.disabled.length));
  }

  function applyPapersView(view) {
    renderPapers(view);
    scanPaperNames = (view.effective || []).slice();
    setPapersStatus(scanPaperNames.length + " 家已启用");
    if (pendingOnly !== null) {
      applyConfigOnly(pendingOnly);
      pendingOnly = null;
    } else if (scanPaperSelected) {
      scanPaperSelected = new Set(scanPaperNames.filter(function (name) {
        return scanPaperSelected.has(name);
      }));
    }
    renderScanPapers();
  }

  function loadPapers() {
    api.get("/api/papers")
      .then(function (view) {
        applyPapersView(view);
      })
      .catch(function () { setPapersStatus("报社列表加载失败"); });
  }

  function collectPaperForm() {
    var name = $("paper-name").value.trim();
    var style = $("paper-style").value;
    var tpl = $("paper-tpl").value.trim();
    if (!name) return { error: "请填写报社名称" };
    if (editingPaperName && name !== editingPaperName) return { error: "编辑时不能修改报社名称；请新建报社" };
    if (name.length > 30) return { error: "名称不能超过 30 个字符" };
    if (!/^https?:\/\//.test(tpl)) return { error: "版面页链接必须以 http:// 或 https:// 开头" };
    if (tpl.indexOf("{a}") === -1) return { error: "版面页链接必须包含 {a} 占位符" };

    var paper = { name: name, style: style, tpl: tpl };
    if (style === "custom") {
      var dateFormat = $("paper-date-format").value.trim();
      if (!dateFormat) return { error: "自定义日期格式不能为空" };
      if (!/^(?:%[Yymd]|[-_./])+$/.test(dateFormat) || dateFormat.indexOf("%d") === -1) {
        return { error: "日期格式只允许 %Y/%y/%m/%d 与 -_./，并且必须包含 %d" };
      }
      paper.date_format = dateFormat;
    }
    var nodePattern = $("paper-node-pattern").value.trim();
    var articlePattern = $("paper-article-pattern").value.trim();
    if (nodePattern !== "") paper.node_pattern = nodePattern;
    if (articlePattern !== "") paper.article_pattern = articlePattern;
    var timeout = $("paper-timeout").value.trim();
    if (timeout !== "") {
      var t = parseInt10(timeout);
      if (isNaN(t) || t < 5 || t > 120) return { error: "超时需为 5 ~ 120 之间的整数" };
      paper.timeout = t;
    }
    var retries = $("paper-retries").value.trim();
    if (retries !== "") {
      var r = parseInt10(retries);
      if (isNaN(r) || r < 0 || r > 6) return { error: "重试需为 0 ~ 6 之间的整数" };
      paper.retries = r;
    }
    var exclude = $("paper-exclude").value.trim();
    if (exclude !== "") paper.node_exclude = exclude;
    return { paper: paper };
  }

  function clearPaperForm() {
    editingPaperName = null;
    [
      "paper-name", "paper-tpl", "paper-date-format", "paper-node-pattern",
      "paper-article-pattern", "paper-timeout", "paper-retries", "paper-exclude"
    ].forEach(function (id) {
      $(id).value = "";
    });
    $("paper-name").readOnly = false;
    $("paper-style").value = "ymd";
    $("paper-form-summary").textContent = "添加报社";
    $("paper-add").textContent = "保存报社";
    updatePaperStyleUI();
    setPapersStatus("—");
  }

  function updatePaperStyleUI() {
    $("paper-date-format-field").hidden = $("paper-style").value !== "custom";
  }

  function fillPaperForm(paper, source) {
    editingPaperName = paper.name || null;
    $("papers-card").open = true;
    $("paper-form-details").open = true;
    $("paper-name").value = paper.name || "";
    $("paper-name").readOnly = true;
    $("paper-style").value = paper.style || "ymd";
    $("paper-date-format").value = paper.date_format || "";
    $("paper-tpl").value = paper.tpl || "";
    $("paper-node-pattern").value = paper.node_pattern || "";
    $("paper-article-pattern").value = paper.article_pattern || "";
    $("paper-timeout").value = paper.timeout === undefined ? "" : paper.timeout;
    $("paper-retries").value = paper.retries === undefined ? "" : paper.retries;
    $("paper-exclude").value = paper.node_exclude || "";
    $("paper-form-summary").textContent = "编辑「" + paper.name + "」";
    $("paper-add").textContent = "保存修改";
    updatePaperStyleUI();
    setPapersStatus(source === "builtin"
      ? "正在修改内置报社「" + paper.name + "」，保存后会创建可撤销的自定义覆盖。"
      : "正在编辑「" + paper.name + "」。");
    window.setTimeout(function () { $("paper-tpl").focus(); }, 0);
  }

  function addPaper() {
    var got = collectPaperForm();
    if (got.error) { setPapersStatus(got.error); return; }
    setPapersStatus("正在保存…");
    api.post("/api/papers", got.paper)
      .then(function (data) {
        applyPapersView(data);
        clearPaperForm();
        $("paper-form-details").open = false;
        setPapersStatus("已保存「" + got.paper.name + "」");
      })
      .catch(function (err) { setPapersStatus("保存失败：" + err.message); });
  }

  function setPaperEnabled(name, enabled, row, button) {
    if (!enabled && !window.confirm("停用报社「" + name + "」？它将不再参与后续扫描，但历史报道和抓取规则都会保留。")) return;
    row.setAttribute("aria-busy", "true");
    button.disabled = true;
    setPapersStatus(enabled ? "正在恢复…" : "正在停用…");
    api.post("/api/papers/status", { name: name, enabled: enabled })
      .then(function (data) {
        applyPapersView(data);
        setPapersStatus((enabled ? "已恢复「" : "已停用「") + name + "」");
      })
      .catch(function (err) {
        row.removeAttribute("aria-busy");
        button.disabled = false;
        setPapersStatus((enabled ? "恢复失败：" : "停用失败：") + err.message);
      });
  }

  function deletePaper(name, source) {
    var restoring = source === "override";
    var question = restoring
      ? "恢复「" + name + "」的内置默认规则？当前自定义覆盖会被删除，启用状态和历史报道不变。"
      : "删除自定义报社「" + name + "」？它将不再参与扫描，但已有历史报道不会被删除。";
    if (!window.confirm(question)) return;
    setPapersStatus("正在删除…");
    api.post("/api/papers/delete", { name: name })
      .then(function (data) {
        applyPapersView(data);
        setPapersStatus(restoring ? "已恢复「" + name + "」的内置默认规则" : "已删除「" + name + "」");
      })
      .catch(function (err) { setPapersStatus("删除失败：" + err.message); });
  }

  function runPaperTest(paper) {
    setPapersHint("试抓中…（会真实联网抓取入口页）");
    api.post("/api/papers/test", paper, { timeout: 150000 })
      .then(function (data) {
        var text = (data.ok ? "✓" : "✗") + " 入口 " + data.entry_code + " · 版面 " + data.sections;
        if (typeof data.sample_articles === "number") text += " · 样例文章 " + data.sample_articles;
        if (data.note) text += " · " + data.note;
        if (data.entry) text += " · " + data.entry;
        setPapersHint(text);
      })
      .catch(function (err) { setPapersHint("试抓失败：" + err.message); });
  }

  function testPaperForm() {
    var got = collectPaperForm();
    if (got.error) { setPapersStatus(got.error); return; }
    runPaperTest(got.paper);
  }

  // 复制「提交并推送 papers.custom.json」命令；clipboard 不可用时回退到选中输入框
  function copyCommitCommand() {
    var input = $("papers-commit-cmd");
    if (input) input.value = PAPERS_COMMIT_CMD;

    function fallback() {
      try { input.focus(); input.select(); } catch (err) { /* 忽略 */ }
      setPapersStatus("无法自动复制，已选中命令，请按 Cmd/Ctrl+C 手动复制。");
    }

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(PAPERS_COMMIT_CMD).then(function () {
        setPapersStatus("已复制安全同步命令（macOS/Linux Shell）。");
      }, fallback);
    } else {
      fallback();
    }
  }

  /* -------------------------------------------------------------- 渲染 */
  function renderResultContext(list) {
    var box = $("result-context");
    var hasFocus = Array.isArray(state.focusKeywords) && state.focusKeywords.length > 0;
    var hasLinked = Boolean(state.linkedFilter);
    box.hidden = !hasFocus && !hasLinked;
    $("result-show-history").hidden = !hasFocus;
    $("result-clear-linked").hidden = !hasLinked;
    if (!hasFocus && !hasLinked) return;

    var parts = [];
    if (hasFocus) parts.push("当前关键词：" + state.focusKeywords.join("、"));
    if (hasLinked) parts.push("图表筛选：" + state.linkedFilter.label);
    $("result-context-title").textContent = parts.join(" · ");
    $("result-context-detail").textContent = list.length
      ? "三处视图已联动，当前显示 " + list.length + " 条命中报道。"
      : (hasFocus
        ? "当前关键词暂无命中；历史归档仍然保留，可切换查看。"
        : "当前图表筛选没有匹配报道，可取消筛选返回。");
  }

  function render() {
    if (!state.data) return;
    var data = state.data;
    var all = data.records;
    var list = sortRecords(filtered());

    $("meta").textContent = "共 " + all.length + " 条命中 · 涉及 " + data.papers.length +
      " 家报社 · 数据更新于 " + data.generated_at;

    renderStats(all, list);
    // 报道、时间趋势与版面位置始终消费同一筛选结果。
    renderChart(list);
    renderLayouts(list);
    renderRows(list);
    renderResultContext(list);

    var visibleCount = Math.min(list.length, state.visibleRows);
    if (list.length > visibleCount) {
      $("shown").textContent = "显示 " + visibleCount + " / " + list.length + " 条" +
        (list.length === all.length ? "" : "（已筛选）");
    } else {
      $("shown").textContent = list.length === all.length
        ? "共 " + all.length + " 条"
        : "显示 " + list.length + " / " + all.length + " 条（已筛选）";
    }
    var resultFooter = $("results-footer");
    resultFooter.hidden = list.length <= visibleCount;
    $("results-limit").textContent = list.length > visibleCount
      ? "已显示前 " + visibleCount + " 条，还有 " + (list.length - visibleCount) + " 条"
      : "";
    if (all.length === 0) {
      renderEmptyState(data);
    } else {
      $("empty").hidden = list.length > 0;
      $("empty").textContent = state.focusKeywords
        ? "当前关键词「" + state.focusKeywords.join("、") + "」暂无命中；历史归档仍保留。"
        : "没有符合条件的命中。";
    }

    var hint = $("range-hint");
    if (hint) {
      if (state.from && state.to && state.from > state.to) {
        hint.textContent = "⚠ 起始日期晚于结束日期，结果为空";
      } else if (state.from || state.to) {
        hint.textContent = (state.from || "…") + " ~ " + (state.to || "…");
      } else {
        hint.textContent = "";
      }
    }

    var foot = $("foot");
    foot.textContent = "";
    foot.appendChild(document.createTextNode("数据目录："));
    foot.appendChild(el("code", null, data.data_dir));
    foot.appendChild(document.createTextNode(" · 归档："));
    foot.appendChild(el("code", null, data.archive_file));
    foot.appendChild(document.createTextNode(" · 页面顶部可直接开始扫描，结果写入该数据目录。"));
  }

  // 命中列表空状态：说明当前数据目录，并引导一键扫描；全新环境额外提示默认关键词
  function renderEmptyState(data) {
    var box = $("empty");
    box.textContent = "";
    box.hidden = false;
    box.appendChild(el("div", "empty-title", "还没有命中记录"));

    var where = el("p", null, "看板读取的数据目录：");
    where.appendChild(el("code", null, data.data_dir));
    box.appendChild(where);

    box.appendChild(el("p", null,
      "在页面顶部填写关键词并点「开始扫描」，完成后本列表会自动刷新。"));

    if (!data.has_web_config) {
      var kws = (data.default_keywords || []).join("、") || "（默认关键词）";
      box.appendChild(el("p", "empty-hint",
        "这是全新环境（尚未生成 state/web_config.json）：默认关键词为「" + kws + "」，默认扫描「今天 + 昨天」。"));
    }
  }

  function renderStats(all, list) {    var data = state.data;
    var seenTotal = Object.keys(data.seen_counts).reduce(function (n, k) {
      return n + data.seen_counts[k];
    }, 0);
    var dates = Array.from(new Set(list.map(function (r) { return r.date; }))).sort();
    var papers = Array.from(new Set(list.map(function (r) { return r.paper; })));

    var cards = [
      ["全部归档", all.length, "历史命中报道"],
      ["当前结果", list.length, list.length === all.length ? "未使用筛选" : "从 " + all.length + " 条中筛出"],
      ["相关报社", papers.length, papers.join("、") || "暂无报社"],
      ["覆盖日期", dates.length, dates.length ? dates[0] + " 至 " + dates[dates.length - 1] : "暂无日期"],
      ["历史已检索", seenTotal, "所有关键词累计，用于识别新增"]
    ];

    var box = $("stats");
    box.textContent = "";
    cards.forEach(function (item) {
      var card = el("article", "stat");
      card.setAttribute("aria-label", item[0] + "：" + item[1] + (item[2] ? "，" + item[2] : ""));
      card.appendChild(el("div", "k", item[0]));
      card.appendChild(el("div", "v", item[1]));
      if (item[2]) {
        var detail = el("div", "x", item[2]);
        detail.title = item[2];
        card.appendChild(detail);
      }
      box.appendChild(card);
    });
  }

  function renderChart(records) {
    var box = $("chart");
    box.textContent = "";
    var counts = {};
    records.forEach(function (r) { counts[r.date] = (counts[r.date] || 0) + 1; });
    var dates = Object.keys(counts).sort();
    if (!dates.length) {
      box.setAttribute("aria-label", "每日命中数量趋势图，暂无数据");
      box.appendChild(el("div", "hint", "暂无数据"));
      return;
    }
    if (dates.length > 90) dates = dates.slice(dates.length - 90);
    var max = dates.reduce(function (m, d) { return Math.max(m, counts[d]); }, 1);
    var total = dates.reduce(function (sum, d) { return sum + counts[d]; }, 0);
    box.setAttribute("aria-label", "每日命中数量趋势图，从 " + dates[0] + " 到 " +
      dates[dates.length - 1] + "，共 " + total + " 条，单日最高 " + max + " 条");
    var full = 96;

    dates.forEach(function (d) {
      var n = counts[d];
      var active = state.linkedFilter && state.linkedFilter.type === "date" && state.linkedFilter.date === d;
      var col = el("button", active ? "bar-col is-active" : "bar-col");
      col.type = "button";
      col.setAttribute("aria-label", d + "，命中 " + n + " 条；" + (active ? "取消当天筛选" : "仅查看当天报道"));
      col.setAttribute("aria-pressed", active ? "true" : "false");
      col.setAttribute("aria-controls", "rows");
      col.addEventListener("click", function () {
        showLinkedResults({ type: "date", key: d, date: d, label: "日期 " + d });
      });
      col.appendChild(el("div", "bar-n", n));
      var bar = el("div", "bar");
      bar.setAttribute("aria-hidden", "true");
      bar.style.height = Math.max(3, Math.round((n / max) * full)) + "px";
      bar.title = d + "：" + n + " 条";
      col.appendChild(bar);
      col.appendChild(el("div", "bar-l", d.slice(5)));
      box.appendChild(col);
    });
  }

  function fallbackLayout(record) {
    var g = record.geometry;
    if (!g || typeof g.top !== "number" || typeof g.bottom !== "number" || g.top >= g.bottom) return null;
    var left = typeof g.left === "number" ? g.left : 0.08;
    var right = typeof g.right === "number" ? g.right : 0.92;
    return {
      basis: g.basis === "area" ? "area" : "mapping",
      aspect_ratio: 0.72,
      items: [{ url: record.url, left: left, top: g.top, right: right, bottom: g.bottom }],
      partial: true
    };
  }

  function pct(value) {
    return Math.max(0, Math.min(100, value * 100)).toFixed(2) + "%";
  }

  function renderLayouts(records) {
    var box = $("layouts");
    var empty = $("layouts-empty");
    var groups = {};
    records.forEach(function (record) {
      var layout = record.layout || fallbackLayout(record);
      if (!layout || !Array.isArray(layout.items) || !layout.items.length) return;
      var key = [record.paper, record.date, record.section_no || "", record.section || "—"].join("\u0001");
      if (!groups[key]) {
        groups[key] = {
          key: key,
          paper: record.paper,
          date: record.date,
          section: record.section || "—",
          layout: layout,
          hitUrls: new Set(),
          titles: []
        };
      }
      if (layout.items.length > groups[key].layout.items.length) groups[key].layout = layout;
      groups[key].hitUrls.add(record.url);
      if (record.title && groups[key].titles.indexOf(record.title) === -1) groups[key].titles.push(record.title);
    });

    box.textContent = "";
    var list = Object.keys(groups).map(function (key) { return groups[key]; });
    list.sort(function (a, b) {
      if (a.date !== b.date) return a.date < b.date ? 1 : -1;
      return a.paper.localeCompare(b.paper, "zh") || a.section.localeCompare(b.section, "zh");
    });
    var layoutCount = list.length;
    var visibleLayouts = list.slice(0, MAX_LAYOUTS);
    $("layouts-summary").textContent = layoutCount
      ? (layoutCount > MAX_LAYOUTS ? "显示 " + MAX_LAYOUTS + " / " + layoutCount + " 个版面" : layoutCount + " 个可绘制版面")
      : "暂无坐标";
    empty.hidden = list.length > 0;
    if (!list.length) {
      empty.textContent = records.length
        ? "这些记录的网站没有提供二维版面坐标；仍会显示“版内第几条”，但不会伪造位置图。重新扫描后，支持坐标的报社会自动出现。"
        : "当前筛选没有命中记录。";
      return;
    }

    visibleLayouts.forEach(function (group) {
      var active = state.linkedFilter && state.linkedFilter.type === "layout" &&
        state.linkedFilter.key === group.key;
      var card = el("article", active ? "layout-item is-active" : "layout-item");
      var page = el("div", "layout-page");
      var ratio = Number(group.layout.aspect_ratio) || 0.72;
      page.style.aspectRatio = String(Math.max(0.35, Math.min(1.5, ratio)));
      page.setAttribute("role", "img");
      page.setAttribute("aria-label", group.paper + " " + group.date + " " + group.section +
        "版面位置图，命中 " + group.hitUrls.size + " 条，共 " + group.layout.items.length + " 个位置");

      group.layout.items.forEach(function (item) {
        var block = el("span", group.hitUrls.has(item.url) ? "layout-block hit" : "layout-block");
        block.style.left = pct(item.left);
        block.style.top = pct(item.top);
        block.style.width = pct(Math.max(0.004, item.right - item.left));
        block.style.height = pct(Math.max(0.004, item.bottom - item.top));
        block.title = group.hitUrls.has(item.url) ? "命中报道" : "本版其他报道";
        page.appendChild(block);
      });
      card.appendChild(page);

      var info = el("div", "layout-info");
      info.appendChild(el("h3", null, group.paper + " · " + group.date));
      info.appendChild(el("p", null, group.section));
      info.appendChild(el("p", "layout-hit", "命中 " + group.hitUrls.size + " 条 / 本版 " + group.layout.items.length + " 个位置"));
      group.titles.slice(0, 3).forEach(function (title) {
        info.appendChild(el("p", null, "· " + title));
      });
      if (group.layout.partial) info.appendChild(el("p", null, "历史记录仅保留命中块，背景文章将在下次扫描后补全。"));
      var viewButton = el("button", active ? "btn subtle layout-action is-active" : "btn subtle layout-action",
        active ? "取消本版筛选" : "查看本版 " + group.hitUrls.size + " 篇报道");
      viewButton.type = "button";
      viewButton.setAttribute("aria-pressed", active ? "true" : "false");
      viewButton.setAttribute("aria-controls", "rows");
      viewButton.addEventListener("click", function () {
        showLinkedResults({
          type: "layout",
          key: group.key,
          urls: new Set(group.hitUrls),
          label: group.paper + " · " + group.date + " · " + group.section
        });
      });
      info.appendChild(viewButton);
      var legend = el("div", "layout-legend");
      var hitKey = el("span", "hit-key");
      hitKey.appendChild(el("i"));
      hitKey.appendChild(document.createTextNode("命中"));
      var normalKey = el("span");
      normalKey.appendChild(el("i"));
      normalKey.appendChild(document.createTextNode("本版其他报道"));
      legend.appendChild(hitKey);
      legend.appendChild(normalKey);
      info.appendChild(legend);
      card.appendChild(info);
      box.appendChild(card);
    });
  }

  function renderRows(records) {
    var body = $("rows");
    body.textContent = "";
    var fragment = document.createDocumentFragment();

    records.slice(0, state.visibleRows).forEach(function (r) {
      var tr = document.createElement("tr");
      var paperCell = el("td", "col-paper", r.paper);
      paperCell.setAttribute("data-label", "报社");
      tr.appendChild(paperCell);
      var dateCell = el("td", "col-date", r.date);
      dateCell.setAttribute("data-label", "日期");
      tr.appendChild(dateCell);
      var sectionText = r.section || "—";
      var position = geomText(r.geometry);
      if (position) sectionText += " · " + position;
      var sectionCell = el("td", "col-section", sectionText);
      sectionCell.setAttribute("data-label", "版面位置");
      tr.appendChild(sectionCell);

      var td = el("td", "title");
      td.setAttribute("data-label", "报道");
      var url = safeUrl(r.url);
      if (url) {
        var a = el("a", null, r.title || r.url);
        a.href = url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        td.appendChild(a);
      } else {
        td.appendChild(el("span", null, r.title));
      }

      (Array.isArray(r.snippets) ? r.snippets : []).slice(0, 2).forEach(function (s) {
        td.appendChild(el("div", "sn", s));
      });

      if (Array.isArray(r.matched) && r.matched.length) {
        var tags = el("div", "tags");
        r.matched.filter(function (k) { return state.keywords.has(k); }).forEach(function (k) {
          tags.appendChild(el("span", "tag", k));
        });
        td.appendChild(tags);
      }

      tr.appendChild(td);
      fragment.appendChild(tr);
    });
    body.appendChild(fragment);
  }

  /* -------------------------------------------------------------- 启动 */
  function scheduleRender() {
    if (renderTimer) window.clearTimeout(renderTimer);
    renderTimer = window.setTimeout(function () {
      renderTimer = null;
      render();
    }, 140);
  }

  function init() {
    $("reload").addEventListener("click", function () { load({ preserve: true }); });
    $("scan-form").addEventListener("submit", function (event) {
      event.preventDefault();
      if (!scanRunning) startScan();
    });
    $("scan-mode").addEventListener("change", function () {
      if ($("scan-mode").value === "range") ensureRangeDefaults();
      updateScanModeUI();
    });
    if ($("scan-cancel")) $("scan-cancel").addEventListener("click", cancelScan);
    $("paper-add").addEventListener("click", addPaper);
    $("paper-create").addEventListener("click", function () {
      clearPaperForm();
      $("papers-card").open = true;
      $("paper-form-details").open = true;
      window.setTimeout(function () { $("paper-name").focus(); }, 0);
    });
    $("paper-test").addEventListener("click", testPaperForm);
    $("paper-clear").addEventListener("click", clearPaperForm);
    $("paper-style").addEventListener("change", updatePaperStyleUI);
    $("papers-commit-cmd").value = PAPERS_COMMIT_CMD;
    $("papers-commit").addEventListener("click", copyCommitCommand);
    $("result-clear-linked").addEventListener("click", function () { clearLinkedFilter(true); });
    $("result-show-history").addEventListener("click", showAllHistory);
    loadScanConfig();
    loadPapers();
    resumeScan();
    $("q").addEventListener("input", function (e) {
      dispatch({ q: e.target.value, linkedFilter: null, visibleRows: INITIAL_ROWS });
    });
    $("from").addEventListener("change", function (e) {
      dispatch({ from: e.target.value, preset: "custom", visibleRows: INITIAL_ROWS, linkedFilter: null });
      updatePresetUI();
    });
    $("to").addEventListener("change", function (e) {
      dispatch({ to: e.target.value, preset: "custom", visibleRows: INITIAL_ROWS, linkedFilter: null });
      updatePresetUI();
    });
    $("sort").addEventListener("change", function (e) {
      dispatch({ sort: e.target.value, visibleRows: INITIAL_ROWS }, { immediate: true });
    });
    $("export").addEventListener("click", openExportDialog);
    $("export-form").addEventListener("submit", saveExport);
    $("export-close").addEventListener("click", closeExportDialog);
    $("export-cancel").addEventListener("click", closeExportDialog);
    $("export-browser").addEventListener("click", downloadExportFallback);
    $("export-directory").addEventListener("input", function (event) {
      updateExportPathPreview(event.target.value.trim());
      if ($("export-status").getAttribute("data-state") === "error") setExportStatus("");
    });
    $("export-dialog").addEventListener("cancel", function (event) {
      if (exportBusy) event.preventDefault();
    });
    $("export-dialog").addEventListener("click", function (event) {
      if (event.target === $("export-dialog")) closeExportDialog();
    });
    $("results-more").addEventListener("click", function () {
      dispatch({ visibleRows: state.visibleRows + ROW_STEP }, { immediate: true });
    });
    Array.prototype.forEach.call($("preset-chips").querySelectorAll("[data-preset]"), function (btn) {
      btn.addEventListener("click", function () { applyPreset(btn.getAttribute("data-preset")); });
    });
    $("reset").addEventListener("click", function () {
      var patch = {
        linkedFilter: null, q: "", from: "", to: "",
        sort: "date-desc", preset: "all", visibleRows: INITIAL_ROWS
      };
      if (state.data) {
        patch.papers = new Set(state.data.papers);
        patch.keywords = new Set(state.focusKeywords || state.data.keywords);
      }
      dispatch(patch);
      $("q").value = "";
      $("from").value = "";
      $("to").value = "";
      $("sort").value = "date-desc";
      buildControls();
      updatePresetUI();
    });
    load();
  }

  init();
})();
