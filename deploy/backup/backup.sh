#!/usr/bin/env bash
# Nightly database backup: dump -> verify -> rotate locally -> copy off-site (if configured).
# Manual run: docker compose -f compose.prod.yml exec backup backup.sh
set -euo pipefail

BACKUP_DIR=${BACKUP_DIR:-/backups}
KEEP_DAYS=${BACKUP_KEEP_DAYS:-14}
export PGHOST=${PGHOST:-db} PGUSER=${PGUSER:-harch} PGDATABASE=${PGDATABASE:-harch}
export PGPASSWORD=${PGPASSWORD:-${POSTGRES_PASSWORD:-}}

log() { echo "$(date '+%F %T') backup: $*"; }

name="harch-$(date +%F_%H%M).sql.gz"
tmp="$BACKUP_DIR/.$name.partial"
trap 'rm -f "$tmp"' EXIT

log "dumping $PGDATABASE@$PGHOST"
# --clean --if-exists: the dump restores over an existing database (restore.sh).
pg_dump --clean --if-exists --no-owner --no-privileges | gzip > "$tmp"
gzip -t "$tmp"
# A real dump always ends with this marker; a truncated one does not.
gunzip -c "$tmp" | tail -n 5 | grep -q "PostgreSQL database dump complete"
mv "$tmp" "$BACKUP_DIR/$name"
log "wrote $name ($(du -h "$BACKUP_DIR/$name" | cut -f1))"

find "$BACKUP_DIR" -maxdepth 1 -name 'harch-*.sql.gz' -mtime +$((KEEP_DAYS - 1)) -print -delete \
  | sed 's/^/removed local /'

if [[ -n "${BACKUP_REMOTE:-}" ]]; then
  rclone copy "$BACKUP_DIR/$name" "$BACKUP_REMOTE"
  rclone delete --min-age "${KEEP_DAYS}d" --include 'harch-*.sql.gz' "$BACKUP_REMOTE"
  log "copied to $BACKUP_REMOTE"
else
  log "BACKUP_REMOTE not set — local copy only"
fi

date '+%F %T' > "$BACKUP_DIR/.last_success"
log "done"
