#!/usr/bin/env bash
# AutoNewspaper 每日 dispatch 触发器
#
# 用途：供 VPS / 任意常开主机的 crontab 调用，打 repository_dispatch 事件给
#       GitHub Actions。私有仓库下原生 schedule 触发器不生效，这是生产入口。
#
# 依赖：bash、curl（Linux 发行版自带 /usr/bin/curl）
# 凭据：PAT 放在独立文件里（不进 crontab，`crontab -l` 不会泄露）
#       fine-grained PAT，仅需对本仓库的 Contents: Read and write
#
# 部署：
#   mkdir -p ~/.newspaperauto && chmod 700 ~/.newspaperauto
#   scp scripts/vps_dispatch.sh <host>:~/.newspaperauto/dispatch.sh
#   ssh <host> 'chmod 700 ~/.newspaperauto/dispatch.sh'
#   # 再把 PAT 写进 ~/.newspaperauto/pat（600），crontab 加一行调用本脚本
#   详见 README「外部定时（VPS crontab）」
#
# 回滚：crontab -e 删掉调用行；rm -rf ~/.newspaperauto
#
# 环境变量（可选）：
#   NEWSPAPER_REPO  仓库，默认 VXXVI/AutoNewspaper
#   NEWSPAPER_HOME  状态目录，默认 $HOME/.newspaperauto
set -uo pipefail

export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

REPO="${NEWSPAPER_REPO:-VXXVI/AutoNewspaper}"
BASE_DIR="${NEWSPAPER_HOME:-$HOME/.newspaperauto}"
PAT_FILE="$BASE_DIR/pat"
LOG_FILE="$BASE_DIR/dispatch.log"
API="https://api.github.com/repos/$REPO/dispatches"

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >>"$LOG_FILE"; }

if [ ! -r "$PAT_FILE" ]; then
  log "ERROR 缺少 PAT 文件 $PAT_FILE，本次未触发（见 README「外部定时（VPS crontab）」）"
  exit 1
fi

PAT=$(tr -d ' \t\r\n' <"$PAT_FILE")
if [ -z "$PAT" ]; then
  log "ERROR PAT 文件为空"
  exit 1
fi

BODY_FILE=$(mktemp) || {
  log "ERROR mktemp 失败"
  exit 1
}
trap 'rm -f "$BODY_FILE"' EXIT

CODE=$(curl -sS -o "$BODY_FILE" -w '%{http_code}' -m 30 -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $PAT" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -d '{"event_type":"scan"}' \
  "$API" 2>>"$LOG_FILE") || CODE="000"

if [ "$CODE" = "204" ]; then
  log "OK http=204 已受理（event_type=scan -> $REPO）"
  exit 0
fi

log "FAIL http=$CODE body=$(head -c 400 "$BODY_FILE" 2>/dev/null | tr '\n' ' ')"
case "$CODE" in
  401) log "提示 401 = PAT 无效或已过期" ;;
  403) log "提示 403 = 权限不足或未勾选该仓库（需 Contents: Read and write）" ;;
  404) log "提示 404 = 仓库名大小写错误，或 PAT 未授权该仓库" ;;
  000) log "提示 000 = 网络不可达" ;;
esac
exit 1
