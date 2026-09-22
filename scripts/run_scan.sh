#!/usr/bin/env bash
# ==============================================================================
# run_scan.sh - 五家报社数字报每日监测启动脚本
# 用于定时任务（launchd / cron）或手动调用，支持环境变量与位置参数传递关键词
# ==============================================================================

set -euo pipefail

# 切换至仓库根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"

# Python 解释器绝对路径
PYTHON_BIN="/Users/quark/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
if [ ! -x "${PYTHON_BIN}" ]; then
    # 兜底 fallback
    PYTHON_BIN="$(command -v python3 || true)"
fi

if [ -z "${PYTHON_BIN}" ] || [ ! -x "${PYTHON_BIN}" ]; then
    echo "错误：未找到可执行的 python3" >&2
    exit 1
fi

# 日志输出目录与日志文件（scan-YYYY-MM-DD.log）
mkdir -p logs
TODAY="$(date +'%Y-%m-%d')"
LOG_FILE="logs/scan-${TODAY}.log"

# 构建关键词参数：
# 1. 若传入位置参数，视作待检索关键词（支持传入多个，依次追加 --kw "..."）
# 2. 若未传入位置参数，检测环境变量 NEWSPAPER_KW
# 3. 若均未提供，不传 --kw，使用 daily_scan.py 默认关键词
KW_ARGS=()
if [ "$#" -gt 0 ]; then
    for kw in "$@"; do
        KW_ARGS+=(--kw "${kw}")
    done
elif [ -n "${NEWSPAPER_KW:-}" ]; then
    # 若环境变量中包含逗号或分号分隔的多个关键词，支持拆分依次传入；单个关键词直接使用
    IFS=',;' read -r -a env_kws <<< "${NEWSPAPER_KW}"
    for kw in "${env_kws[@]}"; do
        trimmed="$(echo "${kw}" | xargs)"
        if [ -n "${trimmed}" ]; then
            KW_ARGS+=(--kw "${trimmed}")
        fi
    done
fi

export PYTHONIOENCODING="utf-8"

echo "=== 开始监测扫描：$(date '+%Y-%m-%d %H:%M:%S') ===" >> "${LOG_FILE}"

if [ "${#KW_ARGS[@]}" -gt 0 ]; then
    "${PYTHON_BIN}" daily_scan.py --days 1 --no-state "${KW_ARGS[@]}" >> "${LOG_FILE}" 2>&1
else
    "${PYTHON_BIN}" daily_scan.py --days 1 --no-state >> "${LOG_FILE}" 2>&1
fi
EXIT_CODE=$?
echo "=== 扫描完成：$(date '+%Y-%m-%d %H:%M:%S')，退出码：${EXIT_CODE} ===" >> "${LOG_FILE}"

exit ${EXIT_CODE}
