/* DOM 小工具：查询、建节点、文本写入与 URL 白名单。
 *
 * 页面遵守「零 innerHTML」：所有来自抓取的数据只用 textContent / createElement
 * 落地，因此这里不提供任何 HTML 解析入口。escapeHtml 仅为「需要拼字符串」的
 * 极少数场景（如未来导出 HTML 报告）保留，日常渲染请直接用 el()/setText()。
 */

export function $(id) {
  return document.getElementById(id);
}

/** 建节点：className / text 均按文本语义写入，绝不解释为 HTML。 */
export function el(tag, className, text) {
  var node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

// 只允许 http/https，杜绝 javascript: 等危险协议
export function safeUrl(raw) {
  try {
    var u = new URL(raw, window.location.origin);
    return (u.protocol === "http:" || u.protocol === "https:") ? u.href : null;
  } catch (err) {
    return null;
  }
}

// 纯字符串转义（无 innerHTML 时几乎用不到；供非 DOM 场景复用）
export function escapeHtml(value) {
  return String(value === undefined || value === null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function setText(node, text) {
  if (!node) return;
  var next = String(text === undefined || text === null ? "" : text);
  if (node.textContent !== next) node.textContent = next;
}

/** 仅在不相等时写 input.value，避免打断正在输入的光标/输入法。 */
export function setInputValue(node, value) {
  if (!node) return;
  var next = value === undefined || value === null ? "" : String(value);
  if (node.value !== next) node.value = next;
}

export function clearNode(node) {
  if (node) node.textContent = "";
}

/** 建节点并挂到 parent，返回新节点：把「createElement + appendChild」压成一行。 */
export function mount(parent, tag, className, text) {
  var node = el(tag, className, text);
  parent.appendChild(node);
  return node;
}

export function appendText(node, text) {
  if (node) node.appendChild(document.createTextNode(String(text === undefined || text === null ? "" : text)));
}

export function setHidden(node, hidden) {
  if (node) node.hidden = Boolean(hidden);
}

export function toggleClass(node, className, on) {
  if (node) node.classList.toggle(className, Boolean(on));
}

/**
 * 生成一枚 chip 按钮。**只做展示与回调**，绝不在这里预先切换 on：
 * aria-pressed 由 state 推导，点击后统一 dispatch，再由 render 重建。
 */
export function chip(options) {
  var className = options.className || "chip";
  var node = el("button", options.on ? className + " on" : className, options.label);
  node.type = "button";
  node.setAttribute("aria-pressed", options.on ? "true" : "false");
  if (options.ariaLabel) node.setAttribute("aria-label", options.ariaLabel);
  if (options.onClick) node.addEventListener("click", options.onClick);
  return node;
}
