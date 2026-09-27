#!/usr/bin/env bash
# ==============================================================================
# scan.command - macOS 双击即跑的「扫一次」启动器（run_scan.sh 的薄封装）
#
# 所有扫描逻辑都在 run_scan.sh（解释器解析 / cd 项目根 / 默认 --days 2 --no-state /
# 参数原样透传），本文件只负责双击时的交互：执行、显示退出状态、等回车再关窗。
# ==============================================================================
DIR="$(cd "$(dirname "$0")" && pwd)"

bash "${DIR}/run_scan.sh" "$@"
status=$?

echo
if [ "${status}" -eq 0 ]; then
    echo "✓ 扫描完成（日志见 logs/）。"
else
    echo "✗ 扫描失败，退出码 ${status}（详见上方输出与 logs/）。"
fi
echo "按回车关闭本窗口…"
read -r _ || true
exit "${status}"
