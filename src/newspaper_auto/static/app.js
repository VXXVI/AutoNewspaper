/* 六家报社数字报监测 · 本地看板
 *
 * 纯原生 JS：无框架、无构建、无 CDN。所有来自抓取的数据（含扫描日志）一律用
 * textContent / createElement 渲染，绝不拼 innerHTML，避免标题/正文/日志里的
 * 内容被当作 HTML 执行。
 */
(function () {
  "use strict";

  var MAX_ROWS = 500;
  var SCAN_POLL_MS = 1200;

  var scanTimer = null;
  var papersView = null;

  var state = {
    data: null,
    q: "",
    papers: new Set(),
    keywords: new Set(),
    from: "",
    to: "",
    sort: "date-desc",
    preset: "all"
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
  function load() {
    $("meta").textContent = "加载中…";
    fetch("/api/hits", { cache: "no-store" })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (data) {
        state.data = data;
        state.papers = new Set(data.papers);
        state.keywords = new Set(data.keywords);
        state.q = "";
        state.from = "";
        state.to = "";
        state.sort = "date-desc";
        state.preset = "all";
        $("q").value = "";
        $("from").value = "";
        $("to").value = "";
        $("sort").value = "date-desc";
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
      paperBox.appendChild(makeChip(name, function (on) {
        if (on) state.papers.add(name); else state.papers.delete(name);
      }));
    });

    var kwBox = $("keyword-chips");
    kwBox.textContent = "";
    data.keywords.forEach(function (kw) {
      kwBox.appendChild(makeChip(kw, function (on) {
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

  function makeChip(label, toggle) {
    var chip = el("button", "chip on", label);
    chip.type = "button";
    chip.setAttribute("aria-pressed", "true");
    chip.addEventListener("click", function () {
      var on = !chip.classList.contains("on");
      chip.classList.toggle("on", on);
      chip.setAttribute("aria-pressed", on ? "true" : "false");
      toggle(on);
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
      if (state.keywords.size && !r.matched.some(function (k) { return state.keywords.has(k); })) return false;
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
      btn.classList.toggle("on", btn.getAttribute("data-preset") === state.preset);
    });
  }

  // 以「数据里最新的日期」为基准，避免用今天算导致历史数据筛不出东西
  function applyPreset(kind) {
    state.preset = kind;
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
    var n = parseInt(value, 10);
    return isNaN(n) ? NaN : n;
  }

  function setScanStatus(text) { $("scan-status").textContent = text; }

  function setScanRunning(running) {
    var btn = $("scan-run");
    btn.disabled = running;
    btn.textContent = running ? "扫描中…" : "立即扫描";
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
    fetch("/api/config", { cache: "no-store" })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
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
        ensureRangeDefaults();
        updateScanModeUI();
      })
      .catch(function () {
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

    if ($("scan-mode").value === "range") {
      var since = $("scan-since").value;
      var until = $("scan-until").value;
      if (!ISO_DATE_RE.test(since) || !ISO_DATE_RE.test(until)) {
        return { error: "请选择合法的起始与结束日期" };
      }
      if (since > until) return { error: "起始日期不能晚于结束日期" };
      if (spanDays(since, until) > 62) return { error: "日期跨度不能超过 62 天" };
      return { body: { keywords: keywords, workers: workers, mode: "range", since: since, until: until } };
    }

    var days = parseInt10($("scan-days").value);
    if (isNaN(days) || days < 1 || days > 62) {
      return { error: "天数需为 1 ~ 62 之间的整数" };
    }
    return { body: { keywords: keywords, workers: workers, mode: "days", days: days } };
  }

  function startScan() {
    var built = buildScanBody();
    if (built.error) { setScanStatus(built.error); return; }
    var body = built.body;

    // 保存配置：失败只提示，不阻断扫描
    fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).catch(function () { /* 忽略：保存失败不影响本次扫描 */ });

    setScanRunning(true);
    setScanStatus("正在提交扫描…");
    $("scan-log").textContent = "";

    fetch("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    })
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (data) {
          if (res.status === 202) { setScanStatus("扫描已启动…"); beginPolling(); return; }
          if (res.status === 409) { setScanStatus("已有扫描在运行，继续查看进度…"); beginPolling(); return; }
          setScanRunning(false);
          setScanStatus("启动失败：" + ((data && data.error) || ("HTTP " + res.status)));
        });
      })
      .catch(function (err) {
        setScanRunning(false);
        setScanStatus("请求失败：" + err.message);
      });
  }

  function beginPolling() {
    if (scanTimer) return;
    pollScan();
    scanTimer = window.setInterval(pollScan, SCAN_POLL_MS);
  }

  function stopPolling() {
    if (scanTimer) { window.clearInterval(scanTimer); scanTimer = null; }
  }

  function renderScanLog(lines) {
    var box = $("scan-log");
    box.textContent = lines.join("\n");   // textContent：日志里可能带回抓取到的网页文本
    box.scrollTop = box.scrollHeight;
  }

  function pollScan() {
    fetch("/api/scan", { cache: "no-store" })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (task) {
        if (Array.isArray(task.log)) renderScanLog(task.log);
        if (task.status === "done") {
          stopPolling();
          setScanRunning(false);
          var code = (task.exit_code === null || task.exit_code === undefined) ? 0 : task.exit_code;
          if (task.error) {
            setScanStatus("扫描完成（退出码 " + code + "）：" + task.error);
          } else {
            setScanStatus("扫描完成（退出码 " + code + "）");
          }
          load();
        } else if (task.status === "error") {
          stopPolling();
          setScanRunning(false);
          setScanStatus("扫描失败：" + (task.error || "未知错误"));
          load();
        } else {
          setScanStatus("扫描中…");
        }
      })
      .catch(function (err) {
        stopPolling();
        setScanRunning(false);
        setScanStatus("轮询失败：" + err.message);
      });
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
        var delBtn = el("button", "chip", "删除");
        delBtn.type = "button";
        delBtn.addEventListener("click", function () { deletePaper(name); });
        actions.appendChild(delBtn);
      }

      row.appendChild(actions);
      box.appendChild(row);
    });
  }

  function loadPapers() {
    fetch("/api/papers", { cache: "no-store" })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (view) { papersView = view; renderPapers(view); })
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
    ["paper-name", "paper-tpl", "paper-timeout", "paper-retries", "paper-exclude"].forEach(function (id) {
      $(id).value = "";
    });
    $("paper-style").value = "ymd";
  }

  function addPaper() {
    var got = collectPaperForm();
    if (got.error) { setPapersStatus(got.error); return; }
    setPapersStatus("正在添加…");
    fetch("/api/papers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(got.paper)
    })
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (data) {
          if (!res.ok) {
            setPapersStatus("添加失败：" + ((data && data.error) || ("HTTP " + res.status)));
            return;
          }
          papersView = data;
          renderPapers(data);
          clearPaperForm();
          setPapersStatus("已添加「" + got.paper.name + "」。已写入 papers.custom.json。" +
            "要让每日 CI 扫描也包含这家，需要把它提交并推送。");
        });
      })
      .catch(function (err) { setPapersStatus("请求失败：" + err.message); });
  }

  function deletePaper(name) {
    setPapersStatus("正在删除…");
    fetch("/api/papers/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name })
    })
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (data) {
          if (!res.ok) {
            setPapersStatus("删除失败：" + ((data && data.error) || ("HTTP " + res.status)));
            return;
          }
          papersView = data;
          renderPapers(data);
          setPapersStatus("已删除「" + name + "」。");
        });
      })
      .catch(function (err) { setPapersStatus("请求失败：" + err.message); });
  }

  function runPaperTest(paper) {
    setPapersHint("试抓中…（会真实联网抓取入口页）");
    fetch("/api/papers/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(paper)
    })
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (data) {
          if (!res.ok) {
            setPapersHint("试抓失败：" + ((data && data.error) || ("HTTP " + res.status)));
            return;
          }
          var text = (data.ok ? "✓" : "✗") + " 入口 " + data.entry_code + " · 版面 " + data.sections;
          if (data.note) text += " · " + data.note;
          setPapersHint(text);
        });
      })
      .catch(function (err) { setPapersHint("试抓请求失败：" + err.message); });
  }

  function testPaperForm() {
    var got = collectPaperForm();
    if (got.error) { setPapersStatus(got.error); return; }
    runPaperTest(got.paper);
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
    renderRows(list);

    $("shown").textContent = list.length === all.length
      ? "共 " + all.length + " 条"
      : "显示 " + Math.min(list.length, MAX_ROWS) + " / " + list.length + " 条（已筛选）";
    $("empty").hidden = list.length > 0;

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
    foot.appendChild(document.createTextNode(" · 可在上方「扫描设置」中一键触发抓取（结果写入该数据目录）。"));
  }

  function renderStats(all, list) {
    var data = state.data;
    var seenTotal = Object.keys(data.seen_counts).reduce(function (n, k) {
      return n + data.seen_counts[k];
    }, 0);
    var dates = Array.from(new Set(list.map(function (r) { return r.date; }))).sort();
    var papers = Array.from(new Set(list.map(function (r) { return r.paper; })));

    var cards = [
      ["归档命中总数", all.length, ""],
      ["当前筛选", list.length, list.length === all.length ? "全部" : "共 " + all.length + " 条中筛选"],
      ["覆盖报社", papers.length, papers.join("、") || "—"],
      ["覆盖日期", dates.length, dates.length ? dates[0] + " ~ " + dates[dates.length - 1] : "—"],
      ["已判定文章", seenTotal, "来自 state/seen.json"]
    ];

    var box = $("stats");
    box.textContent = "";
    cards.forEach(function (item) {
      var card = el("div", "stat");
      card.appendChild(el("div", "k", item[0]));
      card.appendChild(el("div", "v", item[1]));
      if (item[2]) card.appendChild(el("div", "x", item[2]));
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
      box.appendChild(el("div", "hint", "暂无数据"));
      return;
    }
    var max = dates.reduce(function (m, d) { return Math.max(m, counts[d]); }, 1);
    var full = 96;

    dates.forEach(function (d) {
      var n = counts[d];
      var col = el("div", "bar-col");
      col.appendChild(el("div", "bar-n", n));
      var bar = el("div", "bar");
      bar.style.height = Math.max(3, Math.round((n / max) * full)) + "px";
      bar.title = d + "：" + n + " 条";
      col.appendChild(bar);
      col.appendChild(el("div", "bar-l", d.slice(5)));
      box.appendChild(col);
    });
  }

  function renderRows(records) {
    var body = $("rows");
    body.textContent = "";

    records.slice(0, MAX_ROWS).forEach(function (r) {
      var tr = document.createElement("tr");
      tr.appendChild(el("td", "col-paper", r.paper));
      tr.appendChild(el("td", "col-date", r.date));
      var sectionText = r.section || "—";
      var position = geomText(r.geometry);
      if (position) sectionText += " · " + position;
      tr.appendChild(el("td", "col-section", sectionText));

      var td = el("td", "title");
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

      r.snippets.slice(0, 2).forEach(function (s) {
        td.appendChild(el("div", "sn", s));
      });

      if (r.matched.length) {
        var tags = el("div", "tags");
        r.matched.forEach(function (k) { tags.appendChild(el("span", "tag", k)); });
        td.appendChild(tags);
      }

      tr.appendChild(td);
      body.appendChild(tr);
    });
  }

  /* -------------------------------------------------------------- 启动 */
  function init() {
    $("reload").addEventListener("click", load);
    $("scan-run").addEventListener("click", startScan);
    $("scan-mode").addEventListener("change", function () {
      if ($("scan-mode").value === "range") ensureRangeDefaults();
      updateScanModeUI();
    });
    $("paper-add").addEventListener("click", addPaper);
    $("paper-test").addEventListener("click", testPaperForm);
    loadScanConfig();
    loadPapers();
    $("q").addEventListener("input", function (e) { state.q = e.target.value; render(); });
    $("from").addEventListener("change", function (e) {
      state.from = e.target.value; state.preset = "custom"; updatePresetUI(); render();
    });
    $("to").addEventListener("change", function (e) {
      state.to = e.target.value; state.preset = "custom"; updatePresetUI(); render();
    });
    $("sort").addEventListener("change", function (e) { state.sort = e.target.value; render(); });
    $("export").addEventListener("click", exportCsv);
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
