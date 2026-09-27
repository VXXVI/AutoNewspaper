#!/usr/bin/env bash
# ==============================================================================
# run_scan.sh - 数字报每日监测启动脚本（macOS / Linux）
#
# 职责刻意保持很薄：
#   1. 解析 Python 解释器（NEWSPAPER_PYTHON → 项目 .venv → PATH 中的 python3）；
#   2. cd 到项目根；
#   3. 补默认值：始终无状态（--no-state）；未显式给日期时补 --days；
#   4. 把其余参数**原样**透传给 daily_scan.py。
#
# 本脚本**不再维护 daily_scan.py 的选项白名单**：任何 `--xxx` 都直接转发，
# 因此 cli.py 新增选项时无需同步修改这里（历史上白名单滞后已多次导致定时任务报错）。
# 显式给出 --days 或 --since/--until 时，不再注入默认 --days（CLI 里两者互斥）。
#
# 用法示例（全部原样透传）：
#   ./scripts/run_scan.sh --days 3 --workers 4 --only 湛江日报,南方日报
#   ./scripts/run_scan.sh --since 2026-09-20 --until 2026-09-25
#   ./scripts/run_scan.sh --kw 人工智能 --kw 生成式人工智能
#   ./scripts/run_scan.sh --export-dir ./exports --export-format json
#
# 环境变量：
#   NEWSPAPER_PYTHON  Python 解释器绝对路径（默认：项目 .venv → PATH 中的 python3）
#   NEWSPAPER_DAYS    回溯天数默认值（默认 2 = 今天 + 昨天，与 daily_scan.py 一致）
#
# 日志：追加写入 logs/scan-YYYY-MM-DD.log，并同时回显到标准输出（便于双击启动器查看）。
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"

# ---------------------------------------------------------------- Python 解释器
# 不写死任何用户绝对路径：优先显式指定 → 项目虚拟环境 → PATH
resolve_python() {
    if [ -n "${NEWSPAPER_PYTHON:-}" ]; then
        [ -x "${NEWSPAPER_PYTHON}" ] || { echo "NEWSPAPER_PYTHON 不可执行：${NEWSPAPER_PYTHON}" >&2; return 1; }
        printf '%s' "${NEWSPAPER_PYTHON}"
        return 0
    fi
    if [ -x "${PROJECT_DIR}/.venv/bin/python" ]; then
        printf '%s' "${PROJECT_DIR}/.venv/bin/python"
        return 0
    fi
    command -v python3
}

PYTHON_BIN="$(resolve_python)" || { echo "错误：未找到可用的 python3，请安装或设置 NEWSPAPER_PYTHON" >&2; exit 2; }

# ---------------------------------------------------------------- 默认参数
# 只在用户没有显式给出任何日期参数时注入默认天数，避免与 --since/--until 互斥。
DAYS="${NEWSPAPER_DAYS:-2}"
HAS_DATE_ARG=0
for arg in "$@"; do
    case "${arg}" in
        --days|--days=*|--since|--since=*|--until|--until=*) HAS_DATE_ARG=1 ;;
    esac
done

CMD=("${PYTHON_BIN}" daily_scan.py --no-state)
if [ "${HAS_DATE_ARG}" -eq 0 ]; then
    CMD+=(--days "${DAYS}")
fi
# 其余参数原样透传（位置参数 / 未知选项都交给 daily_scan.py 判断）。
CMD+=("$@")

# ---------------------------------------------------------------- 执行并记录日志
mkdir -p logs
LOG_FILE="logs/scan-$(date +'%Y-%m-%d').log"
export PYTHONIOENCODING="utf-8"

echo "=== 开始监测扫描：$(date '+%Y-%m-%d %H:%M:%S') ===" | tee -a "${LOG_FILE}"
echo "命令：${CMD[*]}" | tee -a "${LOG_FILE}"

set +e
"${CMD[@]}" 2>&1 | tee -a "${LOG_FILE}"
EXIT_CODE=${PIPESTATUS[0]}
set -e

echo "=== 扫描完成：$(date '+%Y-%m-%d %H:%M:%S')，退出码：${EXIT_CODE} ===" | tee -a "${LOG_FILE}"

exit "${EXIT_CODE}"
