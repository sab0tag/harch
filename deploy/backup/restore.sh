#!/usr/bin/env bash
# Restore a backup made by backup.sh over the current database.
# Stop web and scheduler first (docs/06_deploy.md, «Відновлення з бекапу»).
#   restore.sh /backups/harch-2026-10-01_0230.sql.gz --yes
set -euo pipefail

file=${1:-}
if [[ -z "$file" || "${2:-}" != "--yes" ]]; then
  echo "usage: restore.sh <file.sql.gz> --yes   (replaces ALL data in the database)" >&2
  exit 2
fi
[[ -f "$file" ]] || { echo "no such file: $file" >&2; exit 1; }

export PGHOST=${PGHOST:-db} PGUSER=${PGUSER:-harch} PGDATABASE=${PGDATABASE:-harch}
export PGPASSWORD=${PGPASSWORD:-${POSTGRES_PASSWORD:-}}

gzip -t "$file"
echo "restoring $file into $PGDATABASE@$PGHOST"
# One transaction: on any error nothing changes.
gunzip -c "$file" | psql --quiet --single-transaction -v ON_ERROR_STOP=1 >/dev/null
echo "restore complete"
