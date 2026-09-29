/* theme.js —— 浅色 / 深色 / 跟随系统 三态主题控制。
 *
 * 只做三件事：读偏好 → 把解析后的主题写到 <html data-theme> → 让切换控件反映当前态。
 * 「跟随系统」在这里解析成显式的 light/dark，所以 style.css 里**只需要一份**深色令牌，
 * 不必再用 prefers-color-scheme 复制整套色板（两处令牌最容易悄悄漂移）。
 *
 * 首屏防闪：<head> 里有一段同口径的内联脚本，在样式表之前就把 data-theme 写好；
 * 这里只是接管后续交互，并同步 <meta name="theme-color">。零依赖、零 innerHTML。
 */

export var THEME_KEY = "newspaper-auto.theme";
export var THEMES = ["auto", "light", "dark"];
export var THEME_LABELS = { auto: "跟随系统", light: "浅色", dark: "深色" };

var DARK_QUERY = "(prefers-color-scheme: dark)";
// 与 index.html 里 <meta name="theme-color"> 的两个候选值、以及 --canvas 同源。
var THEME_COLORS = { light: "#f2f5fa", dark: "#0d1017" };

function readPreference() {
  try {
    var raw = window.localStorage.getItem(THEME_KEY);
    return THEMES.indexOf(raw) !== -1 ? raw : "auto";
  } catch (err) {
    return "auto"; // 隐私模式 / 禁用存储：回落到跟随系统
  }
}

function writePreference(pref) {
  try { window.localStorage.setItem(THEME_KEY, pref); } catch (err) { /* 忽略 */ }
}

/** 把「偏好 + 系统是否深色」解析成最终生效的主题。纯函数，便于单测。 */
export function resolveTheme(pref, systemDark) {
  if (pref === "light" || pref === "dark") return pref;
  return systemDark ? "dark" : "light";
}

function themeColorMetas() {
  return Array.prototype.slice.call(document.querySelectorAll('meta[name="theme-color"]'));
}

/** 顶栏三态控件：按钮的 aria-pressed 由当前偏好推导，点击只改偏好再重放。 */
function themeButtons() {
  var group = document.getElementById("theme-toggle");
  return group ? Array.prototype.slice.call(group.querySelectorAll("[data-theme-value]")) : [];
}

export function initTheme() {
  var media = window.matchMedia ? window.matchMedia(DARK_QUERY) : null;
  var metas = themeColorMetas();
  var buttons = themeButtons();

  function systemDark() { return Boolean(media && media.matches); }

  function apply(pref) {
    var mode = resolveTheme(pref, systemDark());
    document.documentElement.setAttribute("data-theme", mode);
    // 显式主题下媒体查询不再适用：直接按解析结果改写，避免系统深色 + 手动浅色时
    // 移动端地址栏/系统栏仍是深色。
    metas.forEach(function (meta) {
      meta.content = THEME_COLORS[mode];
      meta.removeAttribute("media");
    });
    buttons.forEach(function (btn) {
      var on = btn.getAttribute("data-theme-value") === pref;
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.classList.toggle("on", on);
    });
    var group = document.getElementById("theme-toggle");
    if (group) group.setAttribute("aria-label", "主题：" + THEME_LABELS[pref]);
    return mode;
  }

  buttons.forEach(function (btn) {
    btn.addEventListener("click", function () {
      var pref = btn.getAttribute("data-theme-value");
      if (THEMES.indexOf(pref) === -1) return;
      writePreference(pref);
      apply(pref);
    });
  });

  // 「跟随系统」时系统切换要即时生效；已显式选定则不受系统影响。
  if (media && media.addEventListener) {
    media.addEventListener("change", function () {
      if (readPreference() === "auto") apply("auto");
    });
  }

  apply(readPreference());
}
