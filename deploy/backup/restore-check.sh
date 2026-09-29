#!/usr/bin/env bash
# Prove that a backup really restores — without touching the live database.
# Latest off-site copy (or local, if BACKUP_REMOTE is empty; or the file named) → scratch database
# harch_restore_check → row counts next to the live ones → scratch database dropped.
#   restore-check.sh [harch-YYYY-MM-DD_HHMM.sql.gz]        (ops/harch restore-check)
set -euo pipefail

export PGHOST=${PGHOST:-db} PGUSER=${PGUSER:-harch}
export PGPASSWORD=${PGPASSWORD:-${POSTGRES_PASSWORD:-}}
LIVE=${PGDATABASE:-harch}
SCRATCH=harch_restore_check
TABLES="accounts_user orgstructure_department catalog_product catalog_dish menus_menu orders_dailyorder planning_dailyplan workflow_task consumption_writeoff"

work=$(mktemp -d /tmp/restore-check.XXXXXX)
cleanup() { psql -d postgres -qc "DROP DATABASE IF EXISTS $SCRATCH" >/dev/null 2>&1 || true; rm -rf "$work"; }
trap cleanup EXIT

name=${1:-}
if [[ -n "${BACKUP_REMOTE:-}" ]]; then
  [[ -n "$name" ]] || name=$(rclone -q lsf --include 'harch-*.sql.gz' "$BACKUP_REMOTE" | sort | tail -1)
  where="off-site ($BACKUP_REMOTE)"
  [[ -z "$name" ]] || rclone -q copy "$BACKUP_REMOTE/$name" "$work/"
else
  [[ -n "$name" ]] || name=$(ls /backups | grep -E '^harch-.*\.sql\.gz$' | sort | tail -1 || true)
  where="local (/backups)"
  [[ -z "$name" ]] || cp "/backups/$name" "$work/"
fi
[[ -n "$name" && -f "$work/$name" ]] || { echo "restore-check: no backup found ($where)" >&2; exit 1; }

echo "restore-check: $name from $where"
psql -d postgres -qc "DROP DATABASE IF EXISTS $SCRATCH" -c "CREATE DATABASE $SCRATCH"
PGDATABASE=$SCRATCH restore.sh "$work/$name" --yes

printf '\n%-33s %13s %14s\n' "таблиця" "зараз" "у копії"  # widths in bytes: Cyrillic is 2 bytes a letter
for t in $TABLES; do
  live=$(psql -d "$LIVE" -tAc "select count(*) from $t")
  copy=$(psql -d "$SCRATCH" -tAc "select count(*) from $t")
  note=""; [[ "$live" == "$copy" ]] || note="  ← змінилось після бекапу (це нормально)"
  printf '%-26s %8s %8s%s\n' "$t" "$live" "$copy" "$note"
done
for t in accounts_user catalog_product; do
  [[ "$(psql -d "$SCRATCH" -tAc "select count(*) from $t")" -gt 0 ]] || { echo "restore-check: FAILED — $t is empty in the copy" >&2; exit 1; }
done
echo -e "\nrestore-check: OK — the copy restores; scratch database removed."
