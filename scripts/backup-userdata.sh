#!/usr/bin/env bash
# 备份生产用户数据，按价值/体积分两类独立备份+轮转：
#   - packs/custom：用户定制剧本（YAML，极小、不可重建、曾被误清空丢失过）→ 多留
#   - out/web：生成的表情图（大、可部分重建、历史展示）→ 少留几份够回滚近期
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
KEEP_CUSTOM="${MEMEME_BACKUP_KEEP_CUSTOM:-30}"  # 定制剧本：小且珍贵，留一个月
KEEP_WEB="${MEMEME_BACKUP_KEEP_WEB:-4}"          # 生成图：大，留近 4 份够回滚

mkdir -p "$DEST"

backup_one() {
  local name="$1" path="$2" keep="$3"
  [ -d "$SRC/$path" ] || { echo "  skip $name ($path 不存在)"; return 0; }
  local out="$DEST/$name-$(date +%Y%m%d-%H%M%S).tar.gz"
  tar -czf "$out" -C "$SRC" "$path"
  # 轮转：按时间倒序，删掉第 keep 份之后的（仅同前缀）
  ls -1t "$DEST/$name-"*.tar.gz 2>/dev/null | tail -n +"$((keep + 1))" | xargs -r rm -f
  local kept
  kept=$(ls -1 "$DEST/$name-"*.tar.gz 2>/dev/null | wc -l | tr -d ' ')
  echo "  $name ok: $(du -h "$out" | cut -f1), kept $kept"
}

echo "backup @ $(date +%F\ %T)"
backup_one custom packs/custom "$KEEP_CUSTOM"
backup_one web out/web "$KEEP_WEB"
