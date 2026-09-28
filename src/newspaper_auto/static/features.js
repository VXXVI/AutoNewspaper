/* features.js —— 交互控制器：扫描（SSE + 轮询兜底 + 取消）、报社 CRUD/试抓、导出、系统状态。
 * 只做「读表单/发请求 → dispatch(action)」；结果区一律由 store.subscribe 触发重渲染。
 */
import { A, selectScanNames, selectFiltered, selectSorted, selectExportFilters } from "./store.js";
import { $, setText } from "./dom.js";
import {
  ISO_DATE_RE, contextText, csvCell, parseInt10, shiftDate, spanDays, splitKeywords, todayIso,
} from "./format.js";

var SCAN_POLL_MS = 1200;

export var PAPERS_COMMIT_CMD =
  'git fetch origin main && git pull --rebase --autostash origin main && git add papers.custom.json && git commit -m "chore(papers): 更新报社配置" && git push && git ls-remote origin refs/heads/main';

/* ============================================================ 扫描 */
export function createScanController(ctx) {
  var scanTimer = null, scanStream = null, pollInFlight = false;

  function toneOf(text) {
    if (/失败|未完整|错误|至少|不能|最多|请选择|晚于|超过/.test(text)) return "error";
    if (/完成/.test(text)) return "success";
    if (/正在|扫描中|已启动|接入进度/.test(text)) return "running";
    return "idle";
  }
  function setStatus(text, tone) { ctx.dispatch({ type: A.SCAN_STATUS, text: text, tone: tone || toneOf(text) }); }
  function setRunning(running) { ctx.dispatch({ type: A.SCAN_RUNNING, running: running }); }
  function selectedNames() { return selectScanNames(ctx.getState().scan); }

  function beginStreaming() {
    if (scanStream) return;
    if (typeof window.EventSource !== "function") return beginPolling();
    var source;
    try {
      source = new window.EventSource("/api/scan/events?after=" + encodeURIComponent(ctx.getState().scan.logCursor));
    } catch (err) { return beginPolling(); }
    scanStream = source;
    source.onmessage = function (event) {
      var task;
      try { task = JSON.parse(event.data); } catch (err) { return; }
      consume(task, false);
      if (applyOutcome(task)) return;
      var progress = task.progress || {};
      setStatus(progress.total ? "扫描中：" + progress.completed + " / " + progress.total : "扫描中…");
    };
    source.onerror = function () { stopStreaming(); if (ctx.getState().scan.running) beginPolling(); };
  }
  function stopStreaming() { if (scanStream) { scanStream.close(); scanStream = null; } }
  function schedulePoll(delay) {
    if (scanTimer) return;
    scanTimer = window.setTimeout(function () { scanTimer = null; pollScan(); }, delay);
  }
  function beginPolling() { schedulePoll(0); }
  function stopPolling() { if (scanTimer) { window.clearTimeout(scanTimer); scanTimer = null; } }
  function consume(task, replace) { ctx.dispatch({ type: A.SCAN_INGEST, task: task, replace: replace }); }
  function refreshHistory() { if (ctx.features && ctx.features.system) ctx.features.system.loadScanHistory(); }

  // 终态：停推送、更新状态行、刷新结果；返回是否已是终态。
  function applyOutcome(task) {
    if (task.status !== "done" && task.status !== "error" && task.status !== "cancelled") return false;
    stopPolling(); stopStreaming(); setRunning(false);
    if (task.status === "done") {
      var summary = task.summary;
      setStatus(summary && typeof summary.hits === "number"
        ? "扫描完成：命中 " + summary.hits + " 篇，新增 " + Number(summary.new_hits || 0) + " 篇" : "扫描完成");
    } else if (task.status === "error") {
      setStatus("扫描未完整完成：" + (task.error || "请查看运行日志"));
      var details = $("scan-log-details"); if (details) details.open = true;
    } else {
      setStatus("扫描已取消");
      var box = $("scan-log-details"); if (box) box.open = true;
    }
    ctx.load({ preserve: true, focusKeywords: task.params && task.params.keywords });
    refreshHistory();
    return true;
  }

  function pollScan() {
    if (pollInFlight) return;
    pollInFlight = true;
    ctx.api.get("/api/scan?after=" + encodeURIComponent(ctx.getState().scan.logCursor))
      .then(function (task) {
        consume(task, false);
        if (applyOutcome(task)) return;
        var progress = task.progress || {};
        setStatus(progress.total ? "扫描中：" + progress.completed + " / " + progress.total : "扫描中…");
        schedulePoll(SCAN_POLL_MS);
      })
      .catch(function (err) { stopPolling(); setRunning(false); setStatus("轮询失败：" + err.message); })
      .finally(function () { pollInFlight = false; });
  }

  function buildBody() {
    var keywords = splitKeywords($("scan-kw").value);
    var workers = parseInt10($("scan-workers").value);
    if (!keywords.length) return { error: "请至少填写一个关键词" };
    if (keywords.length > 10) return { error: "关键词最多 10 个" };
    if (keywords.some(function (k) { return k.length > 50; })) return { error: "单个关键词不能超过 50 个字符" };
    if (isNaN(workers) || workers < 1 || workers > 16) return { error: "并发需为 1 ~ 16 之间的整数" };
    var names = ctx.getState().scan.paperNames;
    var selected = selectedNames();
    if (names.length && selected.length === 0) return { error: "请至少选择一家报社" };
    var only = selected.join(",");
    if ($("scan-mode").value === "range") {
      var since = $("scan-since").value, until = $("scan-until").value;
      if (!ISO_DATE_RE.test(since) || !ISO_DATE_RE.test(until)) return { error: "请选择合法的起始与结束日期" };
      if (since > until) return { error: "起始日期不能晚于结束日期" };
      if (spanDays(since, until) > 62) return { error: "日期跨度不能超过 62 天" };
      return { body: { keywords: keywords, workers: workers, mode: "range", since: since, until: until, only: only } };
    }
    var days = parseInt10($("scan-days").value);
    if (isNaN(days) || days < 1 || days > 62) return { error: "天数需为 1 ~ 62 之间的整数" };
    return { body: { keywords: keywords, workers: workers, mode: "days", days: days, only: only } };
  }

  function start() {
    var built = buildBody();
    if (built.error) return setStatus(built.error);
    var body = built.body;
    ctx.api.post("/api/config", body).catch(function () { /* 保存失败不影响本次扫描 */ });
    stopStreaming(); stopPolling();
    ctx.dispatch({ type: A.SCAN_RESET_LOG });
    ctx.dispatch({ type: A.SCAN_CANCELLING, cancelling: false });
    setRunning(true);
    setStatus("正在提交扫描…");
    ctx.api.post("/api/scan", body).then(function (task) {
      consume(task, true);
      setStatus("扫描已启动");
      beginStreaming();
    }).catch(function (err) {
      if (err.status === 409) { setStatus("已有扫描在运行，正在接入进度…"); return resume(); }
      setRunning(false);
      if (err.offline) {
        setStatus("看板服务已停止：请重新启动看板后点「重新连接看板」");
        if (ctx.features && ctx.features.system) ctx.features.system.markOffline();
        return;
      }
      setStatus("启动失败：" + err.message);
    });
  }

  function cancel() {
    ctx.dispatch({ type: A.SCAN_CANCELLING, cancelling: true });
    setStatus("正在取消…");
    ctx.api.post("/api/scan/cancel", {}).catch(function (err) {
      ctx.dispatch({ type: A.SCAN_CANCELLING, cancelling: false });
      setStatus("取消失败：" + err.message);
    });
  }

  function resume() {
    ctx.api.get("/api/scan").then(function (task) {
      if (!task) return;
      ctx.dispatch({ type: A.SCAN_RESET_LOG });
      consume(task, true);
      if (task.status === "running") { setRunning(true); setStatus("自动扫描进行中…"); beginStreaming(); }
      else if (task.status === "done") setStatus("最近一次扫描已完成");
      else if (task.status === "cancelled") setStatus("最近一次扫描已取消");
      else if (task.status === "error") setStatus("最近一次扫描未完整完成：" + (task.error || "请查看日志"));
    }).catch(function () { /* 首屏恢复失败不阻塞其他功能 */ });
  }

  // 回填上次配置；失败用默认值，不阻塞页面。
  function loadConfig() {
    ctx.api.get("/api/config").then(function (cfg) {
      if (Array.isArray(cfg.keywords) && cfg.keywords.length) $("scan-kw").value = cfg.keywords.join(", ");
      if (typeof cfg.days === "number") $("scan-days").value = cfg.days;
      if (typeof cfg.workers === "number") $("scan-workers").value = cfg.workers;
      $("scan-mode").value = cfg.mode === "range" ? "range" : "days";
      if (typeof cfg.since === "string" && ISO_DATE_RE.test(cfg.since)) $("scan-since").value = cfg.since;
      if (typeof cfg.until === "string" && ISO_DATE_RE.test(cfg.until)) $("scan-until").value = cfg.until;
      ctx.dispatch({ type: A.SCAN_CONFIG_ONLY, only: typeof cfg.only === "string" ? cfg.only : "" });
      ensureRangeDefaults(); updateModeUI();
    }).catch(function () {
      ctx.dispatch({ type: A.SCAN_CONFIG_ONLY, only: "" });
      ensureRangeDefaults(); updateModeUI();
    });
  }
  function ensureRangeDefaults() {
    var today = todayIso();
    if (!$("scan-since").value && ISO_DATE_RE.test($("scan-until").value)) $("scan-since").value = shiftDate($("scan-until").value, -1);
    else if (!$("scan-since").value) $("scan-since").value = shiftDate(today, -1);
    if (!$("scan-until").value) $("scan-until").value = today;
  }
  function updateModeUI() {
    var range = $("scan-mode").value === "range";
    $("scan-days-field").hidden = range;
    $("scan-since-field").hidden = !range;
    $("scan-until-field").hidden = !range;
  }
  function onModeChange() { if ($("scan-mode").value === "range") ensureRangeDefaults(); updateModeUI(); }

  return { loadConfig: loadConfig, onModeChange: onModeChange, start: start, cancel: cancel, resume: resume };
}

/* ============================================================ 报社管理 */
export function createPapersController(ctx) {
  function toneOf(text) {
    if (/失败|不能|请先|至少|未找到/.test(text)) return "error";
    if (/已保存|已删除|已停用|已恢复|已启用/.test(text)) return "success";
    return "idle";
  }
  function setStatus(text, tone) { ctx.dispatch({ type: A.PAPERS_STATUS, text: text, tone: tone || toneOf(text) }); }
  function setHint(text) { ctx.dispatch({ type: A.PAPERS_HINT, text: text }); }

  function applyView(view) {
    ctx.dispatch({ type: A.PAPERS_VIEW, view: view });
    ctx.dispatch({ type: A.SCAN_SET_PAPERS, names: (view && view.effective) || [] });
    setStatus(((view && view.effective) || []).length + " 家已启用");
  }
  function load() {
    ctx.api.get("/api/papers").then(applyView).catch(function () { setStatus("报社列表加载失败"); });
  }

  function collect() {
    var name = $("paper-name").value.trim();
    var style = $("paper-style").value;
    var tpl = $("paper-tpl").value.trim();
    var editing = ctx.getState().papersPanel.editing;
    if (!name) return { error: "请填写报社名称" };
    if (editing && name !== editing) return { error: "编辑时不能修改报社名称；请新建报社" };
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

  function updateStyleUI() { $("paper-date-format-field").hidden = $("paper-style").value !== "custom"; }

  function clearForm() {
    ctx.dispatch({ type: A.PAPERS_EDITING, name: null });
    ["paper-name", "paper-tpl", "paper-date-format", "paper-node-pattern",
      "paper-article-pattern", "paper-timeout", "paper-retries", "paper-exclude"]
      .forEach(function (id) { $(id).value = ""; });
    $("paper-name").readOnly = false;
    $("paper-style").value = "ymd";
    $("paper-form-summary").textContent = "添加报社";
    $("paper-add").textContent = "保存报社";
    updateStyleUI();
    setStatus("—");
  }
  function showCreate() {
    clearForm();
    $("papers-card").open = true;
    $("paper-form-details").open = true;
    window.setTimeout(function () { $("paper-name").focus(); }, 0);
  }
  function fillForm(paper, source) {
    ctx.dispatch({ type: A.PAPERS_EDITING, name: paper.name || null });
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
    updateStyleUI();
    setStatus(source === "builtin"
      ? "正在修改内置报社「" + paper.name + "」，保存后会创建可撤销的自定义覆盖。"
      : "正在编辑「" + paper.name + "」。");
    window.setTimeout(function () { $("paper-tpl").focus(); }, 0);
  }
  function add() {
    var got = collect();
    if (got.error) return setStatus(got.error);
    setStatus("正在保存…");
    ctx.api.post("/api/papers", got.paper).then(function (data) {
      applyView(data);
      clearForm();
      $("paper-form-details").open = false;
      setStatus("已保存「" + got.paper.name + "」");
    }).catch(function (err) { setStatus("保存失败：" + err.message); });
  }
  function setEnabled(name, enabled, row, button) {
    if (!enabled && !window.confirm("停用报社「" + name + "」？它将不再参与后续扫描，但历史报道和抓取规则都会保留。")) return;
    if (row) row.setAttribute("aria-busy", "true");
    if (button) button.disabled = true;
    setStatus(enabled ? "正在恢复…" : "正在停用…");
    ctx.api.post("/api/papers/status", { name: name, enabled: enabled }).then(function (data) {
      applyView(data);
      setStatus((enabled ? "已恢复「" : "已停用「") + name + "」");
    }).catch(function (err) {
      if (row) row.removeAttribute("aria-busy");
      if (button) button.disabled = false;
      setStatus((enabled ? "恢复失败：" : "停用失败：") + err.message);
    });
  }
  function remove(name, source) {
    var restoring = source === "override";
    var question = restoring
      ? "恢复「" + name + "」的内置默认规则？当前自定义覆盖会被删除，启用状态和历史报道不变。"
      : "删除自定义报社「" + name + "」？它将不再参与扫描，但已有历史报道不会被删除。";
    if (!window.confirm(question)) return;
    setStatus("正在删除…");
    ctx.api.post("/api/papers/delete", { name: name }).then(function (data) {
      applyView(data);
      setStatus(restoring ? "已恢复「" + name + "」的内置默认规则" : "已删除「" + name + "」");
    }).catch(function (err) { setStatus("删除失败：" + err.message); });
  }
  /* 彻底删除：内置六家也能删，删后从列表消失且没有恢复入口。 */
  function removePaper(name) {
    var question = "彻底删除报社「" + name + "」？\n\n" +
      "· 它将不再出现在列表里，也不再参与扫描；\n" +
      "· 没有「恢复」按钮，要恢复得手工编辑 papers.custom.json；\n" +
      "· 已有的历史报道不会被删除。";
    if (!window.confirm(question)) return;
    setStatus("正在删除…");
    ctx.api.post("/api/papers/remove", { name: name }).then(function (data) {
      applyView(data);
      setStatus("已彻底删除「" + name + "」");
    }).catch(function (err) { setStatus("删除失败：" + err.message); });
  }
  function runTest(paper) {
    setHint("试抓中…（会真实联网抓取入口页）");
    ctx.api.post("/api/papers/test", paper, { timeout: 150000 }).then(function (data) {
      var text = (data.ok ? "✓" : "✗") + " 入口 " + data.entry_code + " · 版面 " + data.sections;
      if (typeof data.sample_articles === "number") text += " · 样例文章 " + data.sample_articles;
      if (data.note) text += " · " + data.note;
      if (data.entry) text += " · " + data.entry;
      setHint(text);
    }).catch(function (err) { setHint("试抓失败：" + err.message); });
  }
  function testForm() {
    var got = collect();
    if (got.error) return setStatus(got.error);
    runTest(got.paper);
  }
  function copyCommitCommand() {
    var input = $("papers-commit-cmd");
    if (input) input.value = PAPERS_COMMIT_CMD;
    function fallback() {
      try { input.focus(); input.select(); } catch (err) { /* 忽略 */ }
      setStatus("无法自动复制，已选中命令，请按 Cmd/Ctrl+C 手动复制。");
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(PAPERS_COMMIT_CMD).then(function () {
        setStatus("已复制安全同步命令（macOS/Linux Shell）。");
      }, fallback);
    } else fallback();
  }

  return {
    load: load, add: add, clearForm: clearForm, showCreate: showCreate, fillForm: fillForm,
    updateStyleUI: updateStyleUI, setEnabled: setEnabled, deletePaper: remove,
    removePaper: removePaper,
    testForm: testForm, runTest: runTest, copyCommitCommand: copyCommitCommand,
  };
}

/* ============================================================ 导出 */
export function createExportController(ctx) {
  function defaultDirectory() {
    var data = ctx.getState().data;
    var base = data && typeof data.data_dir === "string" ? data.data_dir.trim() : "";
    if (!base) return "exports";
    var separator = base.indexOf("\\") !== -1 && base.indexOf("/") === -1 ? "\\" : "/";
    return base.replace(/[\\/]+$/, "") + separator + "exports";
  }
  function updatePathPreview(path, isFile) {
    setText($("export-path-label"), isFile ? "已保存文件" : "目标文件夹");
    setText($("export-path-preview"), path || "请填写保存文件夹");
  }
  function setStatus(text, tone) { ctx.dispatch({ type: A.EXPORT_STATUS, text: text || "", tone: tone || "" }); }
  function setBusy(busy) { ctx.dispatch({ type: A.EXPORT_BUSY, busy: busy }); }
  function setPicker(value) { ctx.dispatch({ type: A.EXPORT_PICKER, value: value }); }

  function open() {
    if (!ctx.getState().data) return;
    var directory = $("export-directory");
    if (!directory.value.trim()) directory.value = defaultDirectory();
    updatePathPreview(directory.value.trim());
    setStatus("");
    var dialog = $("export-dialog");
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
    window.setTimeout(function () { directory.focus(); directory.select(); }, 0);
  }
  function close() {
    if (ctx.getState().exportDialog.busy) return;
    var dialog = $("export-dialog");
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  }
  function downloadCsv() {
    var state = ctx.getState();
    if (!state.data) return;
    var head = ["报社", "日期", "版面", "标题", "链接", "命中关键词", "上下文"];
    var lines = [head.map(csvCell).join(",")];
    selectSorted(state).forEach(function (r) {
      lines.push([r.paper, r.date, r.section || "", r.title, r.url,
        (r.matched || []).filter(function (kw) { return state.keywords.has(kw); }).join(";"),
        contextText(r.snippets)].map(csvCell).join(","));
    });
    var blob = new Blob(["\ufeff" + lines.join("\r\n") + "\r\n"], { type: "text/csv;charset=utf-8" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "newspaper-hits-" + new Date().toISOString().slice(0, 10) + ".csv";
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(a.href);
  }
  function save(event) {
    event.preventDefault();
    var state = ctx.getState();
    if (state.exportDialog.busy || !state.data) return;
    var directory = $("export-directory").value.trim();
    if (!directory) {
      setStatus("请填写保存文件夹的完整路径。", "error");
      $("export-directory").focus();
      return;
    }
    setBusy(true);
    setStatus("正在整理当前筛选结果并写入文件…", "busy");
    ctx.api.post("/api/export", {
      directory: directory, format: $("export-format").value, filters: selectExportFilters(state),
    }, { timeout: 60000 }).then(function (result) {
      var count = typeof result.count === "number" ? result.count : 0;
      var format = typeof result.format === "string" ? result.format.toUpperCase() : "文件";
      updatePathPreview(typeof result.path === "string" ? result.path : directory, true);
      setStatus("导出成功：已保存 " + count + " 条 " + format + " 记录。", "success");
    }).catch(function (err) {
      var unavailable = err.status === 0 || err.status === 404 || err.status === 405 || err.status === 501;
      setStatus(unavailable
        ? "当前服务暂不支持写入指定文件夹。可使用“浏览器下载 CSV”保存当前筛选结果。"
        : "导出失败：" + err.message, "error");
    }).finally(function () { setBusy(false); });
  }
  function fallback() {
    var state = ctx.getState();
    if (!state.data || !selectFiltered(state).length) return;
    downloadCsv();
    setStatus("已交给浏览器下载当前筛选的 CSV；保存位置由浏览器的下载设置决定。", "success");
  }
  /* 弹「看板所在设备」的系统文件夹选择窗口 —— 目录必须是那台机器上的真实路径，
   * 浏览器给不了，所以这一步只能请求服务端。窗口是模态的：不设超时，等用户操作完。
   */
  function browse() {
    var state = ctx.getState();
    if (state.exportDialog.busy || state.exportDialog.picker === "busy" || !state.data) return;
    setPicker("busy");
    setStatus("已请求在看板所在设备上打开文件夹选择窗口，请到那里选择（也可以取消）。", "busy");
    ctx.api.post("/api/export/browse", {
      initial: $("export-directory").value.trim(),
    }, { timeout: 0 }).then(function (result) {
      setPicker("idle");
      if (result && typeof result.path === "string" && result.path) {
        $("export-directory").value = result.path;
        updatePathPreview(result.path);
        setStatus("已选择：" + result.path, "success");
      } else {
        setStatus("已取消选择，仍可手动填写路径。");
      }
    }).catch(function (err) {
      var unsupported = err.status === 501 || Boolean(err.data && err.data.supported === false);
      if (unsupported) {
        // 这台设备弹不出窗口（VPS / 无图形界面）：收起按钮，别再让用户白点。
        setPicker("unsupported");
        setStatus("当前看板所在设备无法弹出系统文件夹选择窗口（常见于 VPS 或无图形界面）。"
          + "请手动填写该设备上的完整路径。", "error");
      } else if (err.status === 409) {
        setPicker("idle");
        setStatus("已有一个文件夹选择窗口在等待操作，请先完成它。", "error");
      } else {
        setPicker("idle");
        setStatus("打开文件夹选择窗口失败：" + err.message, "error");
      }
    });
  }
  function onDirectoryInput(event) {
    updatePathPreview(event.target.value.trim());
    if ($("export-status").getAttribute("data-state") === "error") setStatus("");
  }
  return {
    open: open, close: close, save: save, fallback: fallback, browse: browse,
    onDirectoryInput: onDirectoryInput, downloadCsv: downloadCsv,
  };
}

/* ============================================================ 系统状态 */
export function createSystemController(ctx) {
  function setHealth(ok, health) {
    ctx.dispatch({ type: A.SYSTEM_HEALTH, ok: ok, version: (health && health.version) || "",
      checkedAt: new Date().toISOString() });
  }
  function loadHealth() {
    ctx.api.get("/api/health").then(function (health) {
      setHealth(Boolean(health && health.ok), health);
    }).catch(function () { setHealth(false, null); });
  }
  /* 请求失败且是「连不上」时，把头部/提示区切到离线态，让用户看到自救入口。 */
  function markOffline() { setHealth(false, null); }
  /* 一键重连：先探活，通了就把看板数据重新拉一遍。 */
  function reconnect() {
    return ctx.api.get("/api/health").then(function (health) {
      var ok = Boolean(health && health.ok);
      setHealth(ok, health);
      if (ok) { ctx.load(); }
      return ok;
    }).catch(function () { setHealth(false, null); return false; });
  }
  function loadScanHistory() {
    ctx.api.get("/api/scan/history")
      .then(function (data) { ctx.dispatch({ type: A.SCAN_HISTORY, tasks: (data && data.tasks) || [] }); })
      .catch(function () { ctx.dispatch({ type: A.SCAN_HISTORY, tasks: [] }); });
  }
  return { loadHealth: loadHealth, loadScanHistory: loadScanHistory, markOffline: markOffline, reconnect: reconnect };
}
