#!/usr/bin/env bash
# ==============================================================================
# run_scan.sh - 六家报社数字报每日监测启动脚本
#
# 用途：定时任务（launchd / cron）或手动调用。默认以「无状态」方式运行
#       （daily_scan.py --no-state），不读写 state/ 与 hits/。
#
# 关键词来源（任选其一）：
#   位置参数：  ./scripts/run_scan.sh 广东海洋大学 广海 海大
#   显式选项：  ./scripts/run_scan.sh --kw 广东海洋大学 --kw 广海
#   环境变量：  NEWSPAPER_KW="广东海洋大学,广海" ./scripts/run_scan.sh
#
# 其余选项原样透传给 daily_scan.py，例如：
#   ./scripts/run_scan.sh --days 3 --workers 4 --only 湛江日报,南方日报
#
# 可配置环境变量：
#   NEWSPAPER_PYTHON  Python 解释器绝对路径（默认：项目 .venv → PATH 中的 python3）
#   NEWSPAPER_DAYS    回溯天数（默认 1）
#   NEWSPAPER_KW      关键词，逗号或分号分隔
#
# 日志：追加写入 logs/scan-YYYY-MM-DD.log
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"

die() {
    echo "错误：$*" >&2
    exit 2
}

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

PYTHON_BIN="$(resolve_python)" || die "未找到可用的 python3，请安装或设置 NEWSPAPER_PYTHON"

# ---------------------------------------------------------------- 参数解析
DAYS="${NEWSPAPER_DAYS:-1}"
KW_ARGS=()
PASS_ARGS=()

while [ "$#" -gt 0 ]; do
    case "$1" in
        --)
            shift
            while [ "$#" -gt 0 ]; do KW_ARGS+=(--kw "$1"); shift; done
            ;;
        --kw|-k)
            [ "$#" -ge 2 ] || die "$1 缺少参数"
            KW_ARGS+=(--kw "$2"); shift 2
            ;;
        --kw=*)
            KW_ARGS+=(--kw "${1#--kw=}"); shift
            ;;
        --days)
            [ "$#" -ge 2 ] || die "--days 缺少参数"
            DAYS="$2"; shift 2
            ;;
        --days=*)
            DAYS="${1#--days=}"; shift
            ;;
        --workers|--only|--data-dir)
            [ "$#" -ge 2 ] || die "$1 缺少参数"
            PASS_ARGS+=("$1" "$2"); shift 2
            ;;
        --workers=*|--only=*|--data-dir=*)
            PASS_ARGS+=("$1"); shift
            ;;
        --dry|--full|--json|--csv|-h|--help)
            PASS_ARGS+=("$1"); shift
            ;;
        --*)
            die "未知选项：$1（如需传给 daily_scan.py 的新选项，请在此脚本中登记）"
            ;;
        *)
            # 裸关键词（向后兼容旧用法）
            KW_ARGS+=(--kw "$1"); shift
            ;;
    esac
done

# 位置参数与显式 --kw 均未给出时，退回环境变量
if [ "${#KW_ARGS[@]}" -eq 0 ] && [ -n "${NEWSPAPER_KW:-}" ]; then
    IFS=',;' read -r -a _env_kws <<< "${NEWSPAPER_KW}"
    for _kw in "${_env_kws[@]}"; do
        # 用 sed 去首尾空白（xargs 会误解析引号，不用）
        _kw="$(printf '%s' "${_kw}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        if [ -n "${_kw}" ]; then
            KW_ARGS+=(--kw "${_kw}")
        fi
    done
fi

# ---------------------------------------------------------------- 组命令并执行
mkdir -p logs
LOG_FILE="logs/scan-$(date +'%Y-%m-%d').log"
export PYTHONIOENCODING="utf-8"

# bash 3.2（macOS 自带）在 set -u 下展开空数组会报错，故用 ${arr[@]+...} 惯用法
CMD=("${PYTHON_BIN}" daily_scan.py --days "${DAYS}" --no-state)
CMD+=(${KW_ARGS[@]+"${KW_ARGS[@]}"})
CMD+=(${PASS_ARGS[@]+"${PASS_ARGS[@]}"})

{
    echo "=== 开始监测扫描：$(date '+%Y-%m-%d %H:%M:%S') ==="
    echo "命令：${CMD[*]}"
} >> "${LOG_FILE}"

set +e
"${CMD[@]}" >> "${LOG_FILE}" 2>&1
EXIT_CODE=$?
set -e

echo "=== 扫描完成：$(date '+%Y-%m-%d %H:%M:%S')，退出码：${EXIT_CODE} ===" >> "${LOG_FILE}"

exit "${EXIT_CODE}"
