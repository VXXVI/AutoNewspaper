/* 数字报关键词监测 · 本地看板
 *
 * 纯原生 JS：无框架、无构建、无 CDN。所有来自抓取的数据（含扫描日志）一律用
 * textContent / createElement 渲染，绝不拼 innerHTML，避免标题/正文/日志里的
 * 内容被当作 HTML 执行。
 */
(function () {
  "use strict";

  var INITIAL_ROWS = 50;
  var ROW_STEP = 50;
  var MAX_LAYOUTS = 24;
  var SCAN_POLL_MS = 1200;
  var api = window.NewspaperApi;

  var scanTimer = null;
  var scanPollInFlight = false;
  var scanLogCursor = 0;
  var scanLogLines = [];
  var currentScanTaskId = null;
  var renderTimer = null;

  // 「只扫选中报社」：scanPaperNames = 可选报社（GET /api/papers 的 effective）；
  // scanPaperSelected = null 表示尚未筛选（全选），否则为选中名字集合。
  var scanPaperNames = [];
  var scanPaperSelected = null;
  var pendingOnly = null;      // 从 /api/config 读到的 only，等报社列表到位后再应用
  var scanRunning = false;

  var PAPERS_COMMIT_CMD =
    'git add papers.custom.json && git commit -m "chore(papers): 更新自定义报社" && git push';

  var state = {
    data: null,
    q: "",
    papers: new Set(),
    keywords: new Set(),
    from: "",
    to: "",
    sort: "date-desc",
    preset: "all",
    visibleRows: INITIAL_ROWS
  };

  function $(id) { return document.getElementById(id); }

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

  // 版内位置（旧归档没有 geometry 字段 → 返回空串，静默降级）
  function geomText(g) {
    if (!g || typeof g !== "object") return "";
    var parts = [];
    if (typeof g.index === "number" && typeof g.total === "number" && g.total > 0) {
      parts.push("版内 " + g.index + "/" + g.total);
    }
    if (typeof g.top === "number") parts.push("上版 " + Math.round(g.top * 100) + "%");
    return parts.join(" · ");
  }

  /* -------------------------------------------------------------- 数据 */
  function load(options) {
    options = options || {};
    $("meta").textContent = "加载中…";
    var hadData = Boolean(state.data);
    var allPapersSelected = hadData && state.papers.size === state.data.papers.length;
    var allKeywordsSelected = hadData && state.keywords.size === state.data.keywords.length;
    api.get("/api/hits")
      .then(function (data) {
        state.data = data;
        if (!hadData || !options.preserve) {
          state.papers = new Set(data.papers);
          state.keywords = new Set(data.keywords);
          state.q = "";
          state.from = "";
          state.to = "";
          state.sort = "date-desc";
          state.preset = "all";
          state.visibleRows = INITIAL_ROWS;
          $("q").value = "";
          $("from").value = "";
          $("to").value = "";
          $("sort").value = "date-desc";
        } else {
          state.papers = allPapersSelected
            ? new Set(data.papers)
            : new Set(data.papers.filter(function (name) { return state.papers.has(name); }));
          state.keywords = allKeywordsSelected
            ? new Set(data.keywords)
            : new Set(data.keywords.filter(function (name) { return state.keywords.has(name); }));
        }
        $("version").textContent = "v" + data.version;
        buildControls();
        updatePresetUI();
        render();
      })
      .catch(function (err) {
        $("meta").textContent = "加载失败：" + err.message;
      });
  }

  function buildControls() {
    var data = state.data;

    var paperBox = $("paper-chips");
    paperBox.textContent = "";
    data.papers.forEach(function (name) {
      paperBox.appendChild(makeChip(name, state.papers.has(name), function (on) {
        if (on) state.papers.add(name); else state.papers.delete(name);
      }));
    });

    var kwBox = $("keyword-chips");
    kwBox.textContent = "";
    data.keywords.forEach(function (kw) {
      kwBox.appendChild(makeChip(kw, state.keywords.has(kw), function (on) {
        if (on) state.keywords.add(kw); else state.keywords.delete(kw);
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
      state.visibleRows = INITIAL_ROWS;
      render();
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
      if (state.from && r.date && r.date < state.from) return false;
      if (state.to && r.date && r.date > state.to) return false;
      if (q) {
        var hay = (r.title + " " + r.section + " " + r.snippets.join(" ")).toLowerCase();
        if (hay.indexOf(q) === -1) return false;
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

  /* -------------------------------------------------------------- 日期快捷区间 */
  function dateBounds() {
    var ds = Object.keys(state.data.date_counts).sort();
    return ds.length ? [ds[0], ds[ds.length - 1]] : ["", ""];
  }

  function two(n) { return n < 10 ? "0" + n : "" + n; }

  function shiftDate(iso, days) {
    var p = iso.split("-");
    var d = new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]));
    d.setDate(d.getDate() + days);
    return d.getFullYear() + "-" + two(d.getMonth() + 1) + "-" + two(d.getDate());
  }

  function todayIso() {
    var d = new Date();
    return d.getFullYear() + "-" + two(d.getMonth() + 1) + "-" + two(d.getDate());
  }

  // 两个 ISO 日期之间的「含端点」天数差（用于与后端的 62 天上限对齐）
  function spanDays(since, until) {
    var a = since.split("-").map(Number);
    var b = until.split("-").map(Number);
    var da = Date.UTC(a[0], a[1] - 1, a[2]);
    var db = Date.UTC(b[0], b[1] - 1, b[2]);
    return Math.floor((db - da) / 86400000) + 1;
  }

  var ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

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
    state.preset = kind;
    state.visibleRows = INITIAL_ROWS;
    if (state.data) {
      var latest = dateBounds()[1];
      if (kind === "all") { state.from = ""; state.to = ""; }
      else if (latest && kind === "today") { state.from = latest; state.to = latest; }
      else if (latest && kind === "7d") { state.from = shiftDate(latest, -6); state.to = latest; }
      else if (latest && kind === "30d") { state.from = shiftDate(latest, -29); state.to = latest; }
    }
    $("from").value = state.from;
    $("to").value = state.to;
    updatePresetUI();
    render();
  }

  /* -------------------------------------------------------------- 导出 CSV */
  function csvCell(value) {
    var s = (value === undefined || value === null) ? "" : String(value);
    if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;              // 防 CSV 公式注入
    if (/[",\n\r]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"';
    return s;
  }

  function exportCsv() {
    if (!state.data) return;
    var rows = sortRecords(filtered());
    var head = ["报社", "日期", "版面", "标题", "链接", "命中关键词", "上下文"];
    var lines = [head.map(csvCell).join(",")];
    rows.forEach(function (r) {
      lines.push([
        r.paper, r.date, r.section || "", r.title, r.url,
        r.matched.join(";"), r.snippets.slice(0, 2).join(" ")
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

  /* -------------------------------------------------------------- 扫描设置 */
  // 关键词支持逗号（中英文）、顿号、换行分隔；split + trim + 去空
  function splitKeywords(raw) {
    return raw.split(/[,，、\n\r]+/).map(function (s) { return s.trim(); })
      .filter(function (s) { return s.length > 0; });
  }

  function parseInt10(value) {
    if (typeof value === "string" && value.trim() === "") return NaN;
    var n = Number(value);
    return isFinite(n) && Math.floor(n) === n ? n : NaN;
  }

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
    if (!scanPaperNames.length) { hint.textContent = ""; return; }
    var n = scanSelectedNames().length;
    hint.textContent = n === 0
      ? "⚠ 至少选择一家报社才能扫描"
      : "已选 " + n + " / " + scanPaperNames.length + " 家";
  }

  function updateScanButton() {
    var btn = $("scan-run");
    if (!btn) return;
    var zero = scanPaperNames.length > 0 && scanSelectedNames().length === 0;
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
    scanPaperSelected = selected.size ? selected : null;
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
        beginPolling();
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

  function pollScan() {
    if (scanPollInFlight) return;
    scanPollInFlight = true;
    api.get("/api/scan?after=" + encodeURIComponent(scanLogCursor))
      .then(function (task) {
        consumeScanTask(task, false);
        if (task.status === "done") {
          stopPolling();
          setScanRunning(false);
          var summary = task.summary;
          setScanStatus(summary && typeof summary.hits === "number"
            ? "扫描完成：命中 " + summary.hits + " 篇，新增 " + Number(summary.new_hits || 0) + " 篇"
            : "扫描完成");
          load({ preserve: true });
        } else if (task.status === "error") {
          stopPolling();
          setScanRunning(false);
          setScanStatus("扫描未完整完成：" + (task.error || "请查看运行日志"));
          $("scan-log-details").open = true;
          load({ preserve: true });
        } else {
          var progress = task.progress || {};
          setScanStatus(progress.total
            ? "扫描中：" + progress.completed + " / " + progress.total
            : "扫描中…");
          schedulePoll(SCAN_POLL_MS);
        }
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

  function resumeScan() {
    api.get("/api/scan")
      .then(function (task) {
        if (!task) return;
        scanLogLines = [];
        consumeScanTask(task, true);
        if (task.status === "running") {
          setScanRunning(true);
          setScanStatus("自动扫描进行中…");
          beginPolling();
        } else if (task.status === "done") {
          setScanStatus("最近一次扫描已完成");
        } else if (task.status === "error") {
          setScanStatus("最近一次扫描未完整完成：" + (task.error || "请查看日志"));
        }
      })
      .catch(function () { /* 首屏恢复失败不阻塞其他功能 */ });
  }

  /* -------------------------------------------------------------- 报社管理 */
  function setPapersStatus(text) { $("papers-status").textContent = text; }

  function setPapersHint(text) { $("papers-hint").textContent = text; }

  function renderPapers(view) {
    var box = $("papers-list");
    if (!box) return;
    box.textContent = "";

    var builtin = {};
    var customMap = {};
    (view.builtin || []).forEach(function (name) { builtin[name] = true; });
    (view.custom || []).forEach(function (paper) { customMap[paper.name] = paper; });
    var names = (view.effective || []).slice();

    if (!names.length) {
      box.appendChild(el("span", "hint", "暂无报社"));
      return;
    }

    names.forEach(function (name) {
      var row = el("div", "papers-row");
      row.appendChild(el("span", "paper-name", name));

      var source = customMap[name] ? (builtin[name] ? "覆盖" : "自定义") : "内置";
      row.appendChild(el("span", "paper-src " + (source === "内置" ? "src-builtin" : "src-custom"), source));

      var actions = el("span", "paper-actions");
      var testBtn = el("button", "chip", "试抓");
      testBtn.type = "button";
      testBtn.addEventListener("click", function () {
        // 内置且未覆盖：前端拿不到完整对象，只传 name 由后端解析
        runPaperTest(customMap[name] || { name: name });
      });
      actions.appendChild(testBtn);

      if (customMap[name]) {
        var editBtn = el("button", "chip", "编辑");
        editBtn.type = "button";
        editBtn.addEventListener("click", function () { fillPaperForm(customMap[name]); });
        actions.appendChild(editBtn);

        var delBtn = el("button", "chip danger", "删除");
        delBtn.type = "button";
        delBtn.addEventListener("click", function () { deletePaper(name); });
        actions.appendChild(delBtn);
      }

      row.appendChild(actions);
      box.appendChild(row);
    });

    // 存在自定义 / 覆盖报社时，显示「复制提交命令」（写进 papers.custom.json 后需 commit+push 才进 CI）
    var commitRow = $("papers-commit-row");
    if (commitRow) commitRow.hidden = !(view.custom && view.custom.length);
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
      if (!scanPaperSelected.size) scanPaperSelected = null;
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
    [
      "paper-name", "paper-tpl", "paper-date-format", "paper-node-pattern",
      "paper-article-pattern", "paper-timeout", "paper-retries", "paper-exclude"
    ].forEach(function (id) {
      $(id).value = "";
    });
    $("paper-style").value = "ymd";
    updatePaperStyleUI();
    setPapersStatus("—");
  }

  function updatePaperStyleUI() {
    $("paper-date-format-field").hidden = $("paper-style").value !== "custom";
  }

  function fillPaperForm(paper) {
    $("papers-card").open = true;
    $("paper-form-details").open = true;
    $("paper-name").value = paper.name || "";
    $("paper-style").value = paper.style || "ymd";
    $("paper-date-format").value = paper.date_format || "";
    $("paper-tpl").value = paper.tpl || "";
    $("paper-node-pattern").value = paper.node_pattern || "";
    $("paper-article-pattern").value = paper.article_pattern || "";
    $("paper-timeout").value = paper.timeout === undefined ? "" : paper.timeout;
    $("paper-retries").value = paper.retries === undefined ? "" : paper.retries;
    $("paper-exclude").value = paper.node_exclude || "";
    updatePaperStyleUI();
    setPapersStatus("正在编辑「" + paper.name + "」，保存后会同名覆盖。");
    window.setTimeout(function () { $("paper-name").focus(); }, 0);
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

  function deletePaper(name) {
    if (!window.confirm("删除自定义报社「" + name + "」？内置报社的同名覆盖会恢复为默认配置。")) return;
    setPapersStatus("正在删除…");
    api.post("/api/papers/delete", { name: name })
      .then(function (data) {
        applyPapersView(data);
        setPapersStatus("已删除「" + name + "」");
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
        setPapersStatus("已复制提交命令，粘贴到终端执行即可推送给每日 CI。");
      }, fallback);
    } else {
      fallback();
    }
  }

  /* -------------------------------------------------------------- 渲染 */
  function render() {
    if (!state.data) return;
    var data = state.data;
    var all = data.records;
    var list = sortRecords(filtered());

    $("meta").textContent = "共 " + all.length + " 条命中 · 涉及 " + data.papers.length +
      " 家报社 · 数据更新于 " + data.generated_at;

    renderStats(all, list);
    renderChart(list);
    renderLayouts(list);
    renderRows(list);

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
      $("empty").textContent = "没有符合条件的命中。";
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
      ["已检索文章", seenTotal, "用于识别新增报道"]
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
      var col = el("div", "bar-col");
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
      var key = [record.paper, record.date, record.section || "—"].join("\u0001");
      if (!groups[key]) {
        groups[key] = {
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
      var card = el("article", "layout-item");
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
        r.matched.forEach(function (k) { tags.appendChild(el("span", "tag", k)); });
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
    $("paper-add").addEventListener("click", addPaper);
    $("paper-test").addEventListener("click", testPaperForm);
    $("paper-clear").addEventListener("click", clearPaperForm);
    $("paper-style").addEventListener("change", updatePaperStyleUI);
    $("papers-commit-cmd").value = PAPERS_COMMIT_CMD;
    $("papers-commit").addEventListener("click", copyCommitCommand);
    loadScanConfig();
    loadPapers();
    resumeScan();
    $("q").addEventListener("input", function (e) {
      state.q = e.target.value;
      state.visibleRows = INITIAL_ROWS;
      scheduleRender();
    });
    $("from").addEventListener("change", function (e) {
      state.from = e.target.value; state.preset = "custom"; state.visibleRows = INITIAL_ROWS;
      updatePresetUI(); render();
    });
    $("to").addEventListener("change", function (e) {
      state.to = e.target.value; state.preset = "custom"; state.visibleRows = INITIAL_ROWS;
      updatePresetUI(); render();
    });
    $("sort").addEventListener("change", function (e) {
      state.sort = e.target.value; state.visibleRows = INITIAL_ROWS; render();
    });
    $("export").addEventListener("click", exportCsv);
    $("results-more").addEventListener("click", function () {
      state.visibleRows += ROW_STEP;
      render();
    });
    Array.prototype.forEach.call($("preset-chips").querySelectorAll("[data-preset]"), function (btn) {
      btn.addEventListener("click", function () { applyPreset(btn.getAttribute("data-preset")); });
    });
    $("reset").addEventListener("click", function () {
      if (state.data) {
        state.papers = new Set(state.data.papers);
        state.keywords = new Set(state.data.keywords);
      }
      state.q = "";
      state.from = "";
      state.to = "";
      state.sort = "date-desc";
      state.preset = "all";
      state.visibleRows = INITIAL_ROWS;
      $("q").value = "";
      $("from").value = "";
      $("to").value = "";
      $("sort").value = "date-desc";
      buildControls();
      updatePresetUI();
      render();
    });
    load();
  }

  init();
})();
