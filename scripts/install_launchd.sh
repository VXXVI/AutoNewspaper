#!/usr/bin/env bash
# ==============================================================================
# install_launchd.sh - macOS launchd 定时任务安装与管理脚本（幂等）
#
# 从模板 com.quark.newspaperauto.plist.template 生成 plist（自动替换项目路径
# 与运行时间点），安装到 ~/Library/LaunchAgents/ 并加载。
#
# 用法：
#   ./scripts/install_launchd.sh install [HH:MM ...]   安装并加载（默认 10:00）
#   ./scripts/install_launchd.sh status                查看注册状态与最近日志
#   ./scripts/install_launchd.sh run-now               立即触发一次
#   ./scripts/install_launchd.sh print [HH:MM ...]     打印将要安装的 plist
#   ./scripts/install_launchd.sh uninstall             卸载并移除
#
# 运行时间自定义（可指定多个，每天各跑一次）：
#   ./scripts/install_launchd.sh install 06:30
#   ./scripts/install_launchd.sh install 10:00 12:30 19:00
#   NEWSPAPER_TIMES="10:00,12:30" ./scripts/install_launchd.sh install
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEMPLATE="${SCRIPT_DIR}/com.quark.newspaperauto.plist.template"
LABEL="com.quark.newspaperauto"
AGENTS_DIR="${HOME}/Library/LaunchAgents"
DEST_PLIST="${AGENTS_DIR}/${LABEL}.plist"
UID_NUM="$(id -u)"
DEFAULT_TIME="10:00"

ACTION="${1:-install}"
shift || true
ARGS=("$@")
VALID_TIMES=()

usage() {
    cat <<'EOF'
用法: install_launchd.sh {install|uninstall|status|run-now|print} [HH:MM ...]
  install [HH:MM ...] : 生成并加载定时任务（默认 10:00，可给多个时间）
  uninstall           : 卸载并移除定时任务
  status              : 查看注册状态与最近一次日志
  run-now             : 立即触发一次任务执行
  print [HH:MM ...]   : 打印将要安装的 plist 内容（不落盘）

示例:
  install_launchd.sh install 06:30
  install_launchd.sh install 10:00 12:30 19:00
  NEWSPAPER_TIMES="10:00,12:30" install_launchd.sh install
EOF
}

die() { echo "错误：$*" >&2; exit 1; }

# ---------------------------------------------------------------- 时间解析
parse_times() {
    local -a raw=()
    if [ "$#" -gt 0 ]; then
        raw=("$@")
    elif [ -n "${NEWSPAPER_TIMES:-}" ]; then
        IFS=',;' read -r -a raw <<< "${NEWSPAPER_TIMES}"
    else
        raw=("${DEFAULT_TIME}")
    fi

    VALID_TIMES=()
    local t h m
    for t in ${raw[@]+"${raw[@]}"}; do
        t="$(printf '%s' "${t}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        [ -n "${t}" ] || continue
        [[ "${t}" =~ ^([0-9]{1,2}):([0-9]{1,2})$ ]] || die "时间格式应为 HH:MM，实际：'${t}'"
        h=$((10#${BASH_REMATCH[1]}))
        m=$((10#${BASH_REMATCH[2]}))
        if ((h > 23)); then die "小时超出范围（0-23）：'${t}'"; fi
        if ((m > 59)); then die "分钟超出范围（0-59）：'${t}'"; fi
        VALID_TIMES+=("${h}:${m}")
    done
    [ "${#VALID_TIMES[@]}" -gt 0 ] || die "未提供有效的运行时间"
}

# ---------------------------------------------------------------- 生成 plist
_emit_hour_minute() {
    local indent="$1" time="$2"
    printf '%s<key>Hour</key>\n%s<integer>%s</integer>\n' "${indent}" "${indent}" "${time%%:*}"
    printf '%s<key>Minute</key>\n%s<integer>%s</integer>\n' "${indent}" "${indent}" "${time##*:}"
}

build_schedule() {
    if [ "${#VALID_TIMES[@]}" -eq 1 ]; then
        echo "    <key>StartCalendarInterval</key>"
        echo "    <dict>"
        _emit_hour_minute "        " "${VALID_TIMES[0]}"
        echo "    </dict>"
    else
        echo "    <key>StartCalendarInterval</key>"
        echo "    <array>"
        for t in "${VALID_TIMES[@]}"; do
            echo "        <dict>"
            _emit_hour_minute "            " "${t}"
            echo "        </dict>"
        done
        echo "    </array>"
    fi
}

render_plist() {
    [ -f "${TEMPLATE}" ] || die "模板不存在：${TEMPLATE}"
    local content schedule
    content="$(cat "${TEMPLATE}")"
    content="${content//\{\{PROJECT_DIR\}\}/$PROJECT_DIR}"
    schedule="$(build_schedule)"
    content="${content//\{\{START_CALENDAR_INTERVAL\}\}/$schedule}"
    printf '%s\n' "${content}"
}

# ---------------------------------------------------------------- 加载/卸载
do_unload() {
    launchctl bootout "gui/${UID_NUM}/${LABEL}" 2>/dev/null || true
    if [ -f "${DEST_PLIST}" ]; then
        launchctl unload "${DEST_PLIST}" 2>/dev/null || true
    fi
}

do_load() {
    if ! launchctl bootstrap "gui/${UID_NUM}" "${DEST_PLIST}" 2>/dev/null; then
        launchctl load -w "${DEST_PLIST}"
    fi
    launchctl enable "gui/${UID_NUM}/${LABEL}" 2>/dev/null || true
}

show_status() {
    echo "=== launchd 服务状态（${LABEL}）==="
    if launchctl print "gui/${UID_NUM}/${LABEL}" >/dev/null 2>&1; then
        launchctl print "gui/${UID_NUM}/${LABEL}" \
            | grep -E '[[:space:]](state|program|last exit code) = ' || true
    else
        echo "未注册 ${LABEL}（可执行 '$0 install' 安装）"
    fi
    echo
    echo "=== 最近日志 ==="
    local latest
    latest="$(ls -1t "${PROJECT_DIR}"/logs/scan-*.log 2>/dev/null | head -n 1 || true)"
    if [ -n "${latest}" ]; then
        echo "--- ${latest} ---"
        tail -n 12 "${latest}"
    else
        echo "（暂无 logs/scan-*.log）"
    fi
}

# ---------------------------------------------------------------- 分发
case "${ACTION}" in
    install)
        parse_times ${ARGS[@]+"${ARGS[@]}"}
        mkdir -p "${AGENTS_DIR}" "${PROJECT_DIR}/logs"
        render_plist > "${DEST_PLIST}"
        if command -v plutil >/dev/null 2>&1; then
            plutil -lint "${DEST_PLIST}" >/dev/null || die "生成的 plist 校验失败：${DEST_PLIST}"
        fi
        do_unload
        do_load
        DISPLAY_TIMES=()
        for t in "${VALID_TIMES[@]}"; do
            DISPLAY_TIMES+=("$(printf '%02d:%02d' "${t%%:*}" "${t##*:}")")
        done
        echo "✓ 已安装并加载：${DEST_PLIST}"
        echo "  运行时间：每天 ${DISPLAY_TIMES[*]}（共 ${#VALID_TIMES[@]} 次）"
        echo "  可用 '$0 status' 查看、'$0 run-now' 立即测试。"
        ;;
    print)
        parse_times ${ARGS[@]+"${ARGS[@]}"}
        render_plist
        ;;
    uninstall)
        do_unload
        if [ -f "${DEST_PLIST}" ]; then
            rm -f "${DEST_PLIST}"
            echo "✓ 已移除：${DEST_PLIST}"
        else
            echo "未发现已安装的 plist：${DEST_PLIST}"
        fi
        ;;
    status)
        show_status
        ;;
    run-now)
        launchctl print "gui/${UID_NUM}/${LABEL}" >/dev/null 2>&1 || die "尚未安装，请先执行 '$0 install'"
        launchctl kickstart -k "gui/${UID_NUM}/${LABEL}" 2>/dev/null || launchctl start "${LABEL}"
        echo "✓ 已触发，稍后查看：${PROJECT_DIR}/logs/"
        ;;
    *)
        usage
        exit 1
        ;;
esac
