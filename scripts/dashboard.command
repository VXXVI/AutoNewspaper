#!/usr/bin/env bash
# ==============================================================================
# dashboard.command - macOS 双击即开的「本地看板」启动器
#
# 用 Finder 双击本文件：启动本地看板（优先 http://127.0.0.1:8788）并自动打开浏览器。
# 端口被占用时自动改用空闲端口（系统分配），无需手动处理；
# 也可用环境变量 NEWSPAPER_PORT 或 --port 指定端口。
#
# ⚠️ 看板不是只读的：可在页面「扫描设置」里一键触发**真实扫描**，会写入 state/
#    （含 web_config.json、seen.json）与 hits/；「报社」卡片里的「试抓」也会联网。
# 关闭窗口（或按 Ctrl+C）即停止服务。
# ==============================================================================
cd "$(dirname "$0")/.." || exit 1

PY="${NEWSPAPER_PYTHON:-}"
if [ -z "${PY}" ]; then
    if [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"; else PY="$(command -v python3)"; fi
fi
if [ -z "${PY}" ]; then
    echo "未找到 python3，请先安装或设置 NEWSPAPER_PYTHON。"
    echo "按回车关闭窗口。"; read -r _; exit 1
fi

PORT="${NEWSPAPER_PORT:-8788}"
if "${PY}" dashboard.py --port "${PORT}" --open; then
    exit 0
fi
echo
echo "端口 ${PORT} 不可用，改用系统自动分配的空闲端口重试…"
"${PY}" dashboard.py --port 0 --open || {
    echo
    echo "看板启动失败，按回车关闭窗口。"
    read -r _
}
