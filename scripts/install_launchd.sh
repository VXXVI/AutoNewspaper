#!/usr/bin/env bash
# ==============================================================================
# install_launchd.sh - macOS launchd 定时任务安装与管理脚本（幂等）
# 将 com.quark.newspaperauto.plist 安装到 ~/Library/LaunchAgents/ 并刷新加载
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_SRC="${SCRIPT_DIR}/com.quark.newspaperauto.plist"
PLIST_NAME="com.quark.newspaperauto.plist"
USER_AGENTS_DIR="${HOME}/Library/LaunchAgents"
DEST_PLIST="${USER_AGENTS_DIR}/${PLIST_NAME}"
LABEL="com.quark.newspaperauto"

mkdir -p "${USER_AGENTS_DIR}"

usage() {
    echo "用法: $0 {install|uninstall|status|run-now}"
    echo "  install   : 安装并加载定时任务（每天 07:00 运行）"
    echo "  uninstall : 卸载并移除定时任务"
    echo "  status    : 查看当前任务运行状态"
    echo "  run-now   : 立即触发一次任务执行"
    exit 1
}

# 辅助函数：unload（兼容 legacy unload 与 bootout）
do_unload() {
    # 尝试现代 bootout（macOS 10.11+ / GUI domain）
    local uid
    uid="$(id -u)"
    launchctl bootout "gui/${uid}/${LABEL}" 2>/dev/null || true
    # 兼容传统 unload
    if [ -f "${DEST_PLIST}" ]; then
        launchctl unload "${DEST_PLIST}" 2>/dev/null || true
    fi
}

# 辅助函数：load（兼容 legacy load 与 bootstrap）
do_load() {
    local uid
    uid="$(id -u)"
    # 先尝试现代 bootstrap，若失败则 fallback 到传统 load
    if ! launchctl bootstrap "gui/${uid}" "${DEST_PLIST}" 2>/dev/null; then
        launchctl load "${DEST_PLIST}"
    fi
}

case "${1:-install}" in
    install)
        echo "正在安装 launchd 服务..."
        if [ ! -f "${PLIST_SRC}" ]; then
            echo "错误：模板文件不存在：${PLIST_SRC}" >&2
            exit 1
        fi

        # 幂等处理：先卸载旧服务
        do_unload

        # 拷贝最新 plist
        cp -f "${PLIST_SRC}" "${DEST_PLIST}"

        # 重新加载
        do_load

        echo "✓ 成功安装并加载：${DEST_PLIST}"
        echo "  可通过 '$0 status' 查看状态，或 '$0 run-now' 立即测试。"
        ;;
    uninstall)
        echo "正在卸载 launchd 服务..."
        do_unload
        if [ -f "${DEST_PLIST}" ]; then
            rm -f "${DEST_PLIST}"
            echo "✓ 已移除：${DEST_PLIST}"
        else
            echo "未发现安装的 plist 文件：${DEST_PLIST}"
        fi
        ;;
    status)
        echo "=== Launchd 服务状态 ==="
        if launchctl list | grep -q "${LABEL}"; then
            launchctl list | grep "${LABEL}"
        else
            echo "未注册 ${LABEL}"
        fi
        ;;
    run-now)
        echo "立即触发执行..."
        launchctl start "${LABEL}" 2>/dev/null || launchctl kickstart -k "gui/$(id -u)/${LABEL}"
        echo "✓ 已发送启动指令，可检查 logs/ 目录下最新日志。"
        ;;
    *)
        usage
        ;;
esac
