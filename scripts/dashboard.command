#!/usr/bin/env bash
# ==============================================================================
# dashboard.command - macOS 双击即开的「本地看板」启动器（薄封装）
#
# 端口回退逻辑唯一实现于 scripts/launch_dashboard.py；本文件只负责双击场景：
# 解析 Python 解释器、cd 项目根、把参数原样透传过去。
# 也可用环境变量 NEWSPAPER_PORT 或 --port 指定端口；看板默认**后台**运行（--detach），
# 关掉本窗口不会停止它（停止：scripts/launch_dashboard.py --stop）。
#
# ⚠️ 看板不是只读的：可在页面「扫描设置」里一键触发**真实扫描**，会写入 state/
#    （含 web_config.json、seen.json）与 hits/；「报社」卡片里的「试抓」也会联网。
# ==============================================================================
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "${DIR}/.." || exit 1

PY="${NEWSPAPER_PYTHON:-}"
if [ -z "${PY}" ]; then
    if [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"; else PY="$(command -v python3)"; fi
fi
if [ -z "${PY}" ]; then
    echo "未找到 python3，请先安装或设置 NEWSPAPER_PYTHON。"
    echo "按回车关闭窗口。"; read -r _; exit 1
fi

exec "${PY}" scripts/launch_dashboard.py --detach "$@"
