#!/usr/bin/env bash
# ==============================================================================
# scan.command - macOS 双击即跑的「扫一次」启动器
#
# 用 Finder 双击本文件即可：在当前项目上跑一次「今天 + 昨天」的无状态扫描，
# 结果直接打印在终端窗口里。无状态 = 不读写 state/seen.json、不产 hits/*，
# 与 GitHub Actions 互不影响（详见 README「定时任务」）。
#
# 如需带关键词 / 改天数，可改为调用 scripts/run_scan.sh 传参，或直接命令行运行。
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

echo "=== AutoNewspaper 扫描（今天 + 昨天，无状态）==="
echo "解释器：${PY}"
echo

if "${PY}" daily_scan.py --days 2 --no-state; then
    echo
    echo "✓ 扫描完成（日志另见 logs/）。"
else
    echo
    echo "✗ 扫描失败，退出码 $?。"
fi

echo
echo "按回车关闭本窗口…"
read -r _
