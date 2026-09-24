/* 纯函数工具模块：日期、CSV 转义、关键词切分、版内位置文本。
 *
 * 这些函数不依赖 DOM 与全局状态，抽出为原生 ES Module，便于单独推理/测试；
 * 页面仍保持「无框架、无构建、无 CDN」。
 */

export var ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export function two(n) { return n < 10 ? "0" + n : "" + n; }

export function shiftDate(iso, days) {
  var p = iso.split("-");
  var d = new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]));
  d.setDate(d.getDate() + days);
  return d.getFullYear() + "-" + two(d.getMonth() + 1) + "-" + two(d.getDate());
}

export function todayIso() {
  var d = new Date();
  return d.getFullYear() + "-" + two(d.getMonth() + 1) + "-" + two(d.getDate());
}

// 两个 ISO 日期之间的「含端点」天数差（与后端的 62 天上限对齐）
export function spanDays(since, until) {
  var a = since.split("-").map(Number);
  var b = until.split("-").map(Number);
  var da = Date.UTC(a[0], a[1] - 1, a[2]);
  var db = Date.UTC(b[0], b[1] - 1, b[2]);
  return Math.floor((db - da) / 86400000) + 1;
}

// CSV 单元格：防公式注入（=+-@ 开头）并做引号转义
export function csvCell(value) {
  var s = (value === undefined || value === null) ? "" : String(value);
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
  if (/[",\n\r]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"';
  return s;
}

// 版内位置文本（旧归档没有 geometry → 返回空串，静默降级）
export function geomText(g) {
  if (!g || typeof g !== "object") return "";
  var parts = [];
  if (typeof g.index === "number" && typeof g.total === "number" && g.total > 0) {
    parts.push("版内 " + g.index + "/" + g.total);
  }
  if (typeof g.top === "number") parts.push("上版 " + Math.round(g.top * 100) + "%");
  return parts.join(" · ");
}

// 关键词分隔：逗号（中英文）、顿号、换行；去空并去重
export function splitKeywords(raw) {
  var result = [];
  raw.split(/[,，、\n\r]+/).map(function (s) { return s.trim(); })
    .filter(function (s) { return s.length > 0; })
    .forEach(function (keyword) {
      if (result.indexOf(keyword) === -1) result.push(keyword);
    });
  return result;
}

export function parseInt10(value) {
  if (typeof value === "string" && value.trim() === "") return NaN;
  var n = Number(value);
  return isFinite(n) && Math.floor(n) === n ? n : NaN;
}
