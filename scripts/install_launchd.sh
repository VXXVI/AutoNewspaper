#!/usr/bin/env bash
# ==============================================================================
# install_launchd.sh - macOS launchd 定时任务安装与管理脚本（幂等）
#
# 从模板 com.quark.newspaperauto.plist.template 生成 plist（自动替换项目路径），
# 安装到 ~/Library/LaunchAgents/ 并加载。每天 07:00 运行 scripts/run_scan.sh。
#
# 用法：
#   ./scripts/install_launchd.sh install     安装并加载（每天 07:00）
#   ./scripts/install_launchd.sh status      查看注册状态与最近日志
#   ./scripts/install_launchd.sh run-now     立即触发一次
#   ./scripts/install_launchd.sh print       打印将要安装的 plist 内容
#   ./scripts/install_launchd.sh uninstall   卸载并移除
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEMPLATE="${SCRIPT_DIR}/com.quark.newspaperauto.plist.template"
LABEL="com.quark.newspaperauto"
AGENTS_DIR="${HOME}/Library/LaunchAgents"
DEST_PLIST="${AGENTS_DIR}/${LABEL}.plist"
UID_NUM="$(id -u)"

usage() {
    cat <<'EOF'
用法: install_launchd.sh {install|uninstall|status|run-now|print}
  install   : 生成并加载定时任务（每天 07:00 运行 run_scan.sh）
  uninstall : 卸载并移除定时任务
  status    : 查看注册状态与最近一次日志
  run-now   : 立即触发一次任务执行
  print     : 打印将要安装的 plist 内容（不落盘）
EOF
}

die() { echo "错误：$*" >&2; exit 1; }

# 生成 plist：把模板中的 {{PROJECT_DIR}} 替换为真实路径
render_plist() {
    [ -f "${TEMPLATE}" ] || die "模板不存在：${TEMPLATE}"
    local escaped="${PROJECT_DIR//&/\\&}"
    sed -e "s|{{PROJECT_DIR}}|${escaped}|g" "${TEMPLATE}"
}

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

case "${1:-install}" in
    install)
        mkdir -p "${AGENTS_DIR}" "${PROJECT_DIR}/logs"
        render_plist > "${DEST_PLIST}"
        if command -v plutil >/dev/null 2>&1; then
            plutil -lint "${DEST_PLIST}" >/dev/null || die "生成的 plist 校验失败：${DEST_PLIST}"
        fi
        do_unload
        do_load
        echo "✓ 已安装并加载：${DEST_PLIST}"
        echo "  每天 07:00 自动运行；可用 '$0 status' 查看、'$0 run-now' 立即测试。"
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
    print)
        render_plist
        ;;
    *)
        usage
        exit 1
        ;;
esac
