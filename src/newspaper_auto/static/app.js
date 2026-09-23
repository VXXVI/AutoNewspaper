/* 六家报社数字报监测 · 本地只读看板
 *
 * 纯原生 JS：无框架、无构建、无 CDN。所有来自抓取的数据一律用
 * textContent / createElement 渲染，绝不拼 innerHTML，避免标题/正文里的
 * 内容被当作 HTML 执行。
 */
(function () {
  "use strict";

  var MAX_ROWS = 500;

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

  function shiftDate(iso, days) {
    var p = iso.split("-");
    var d = new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]));
    d.setDate(d.getDate() + days);
    function two(n) { return n < 10 ? "0" + n : "" + n; }
    return d.getFullYear() + "-" + two(d.getMonth() + 1) + "-" + two(d.getDate());
  }

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
    foot.appendChild(document.createTextNode(" · 本页只读，不会触发抓取。"));
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
      tr.appendChild(el("td", "col-section", r.section || "—"));

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
