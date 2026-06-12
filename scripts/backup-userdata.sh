#!/usr/bin/env bash
# 备份生产用户数据：定制剧本(packs/custom)与生成任务(out/web)。
# 动机：packs/custom 曾被误清空、用户定制包永久丢失（单副本脆弱存储）。
# 备份目录独立于 SRC，所以"清空/误重置 packs/custom"不会连累备份。
#
# 装到 VDS（在服务器上跑，非本地）:
#   scp scripts/backup-userdata.sh memeplanet-vds:/opt/memeplanet/scripts/
#   ssh memeplanet-vds 'chmod +x /opt/memeplanet/scripts/backup-userdata.sh \
#     && ( crontab -l 2>/dev/null; echo "17 4 * * * /opt/memeplanet/scripts/backup-userdata.sh >> /var/log/mp-backup.log 2>&1" ) | crontab -'
# 手动验证一次: ssh memeplanet-vds /opt/memeplanet/scripts/backup-userdata.sh
set -euo pipefail

SRC="${MEMEME_BACKUP_SRC:-/opt/memeplanet}"
DEST="${MEMEME_BACKUP_DEST:-/opt/memeplanet-backups}"
KEEP="${MEMEME_BACKUP_KEEP:-14}"   # 保留最近 N 份

mkdir -p "$DEST"
ts="$(date +%Y%m%d-%H%M%S)"
out="$DEST/userdata-$ts.tar.gz"

# 只打包存在的子目录，避免空目录报错
paths=()
[ -d "$SRC/packs/custom" ] && paths+=("packs/custom")
[ -d "$SRC/out/web" ] && paths+=("out/web")
if [ "${#paths[@]}" -eq 0 ]; then
  echo "nothing to back up under $SRC" >&2
  exit 0
fi

tar -czf "$out" -C "$SRC" "${paths[@]}"
# 轮转：按时间倒序，删掉第 KEEP 份之后的
ls -1t "$DEST"/userdata-*.tar.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f

echo "backup ok: $out ($(du -h "$out" | cut -f1)), kept $(ls -1 "$DEST"/userdata-*.tar.gz | wc -l | tr -d ' ')"
