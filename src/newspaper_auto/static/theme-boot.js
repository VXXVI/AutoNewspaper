/* 首屏防闪：在样式表之前把主题落到 <html data-theme>，避免刷新时先亮后暗闪一下。
 *
 * 独立成外部脚本而不是内联：看板响应带 `script-src 'self'` 的 CSP，内联脚本会被
 * 浏览器直接拒绝执行（控制台报 CSP 违规），防闪逻辑等于没生效。
 * 这里必须是**阻塞式**普通脚本（不能 defer），否则仍会晚于首次绘制。
 * localStorage 键名与解析规则必须与 theme.js 保持一致。
 */
(function () {
  "use strict";
  var pref = "auto";
  try { pref = localStorage.getItem("newspaper-auto.theme") || "auto"; } catch (err) { /* 隐私模式忽略 */ }
  var dark = pref === "dark" ||
    (pref !== "light" && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
})();
