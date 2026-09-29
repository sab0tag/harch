#!/usr/bin/env bash
# S6-07: update production to origin/main in one step (docs/06_deploy.md, «Обновление»).
# On the server:  /opt/harch/deploy/update.sh
# From a laptop:  ssh deploy@<server> /opt/harch/deploy/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose -f compose.prod.yml "$@"; }
log() { echo "$(date '+%F %T') update: $*"; }

git pull --ff-only
log "code at $(git log --oneline -1)"
compose build
compose run --rm web python manage.py migrate --noinput
compose up -d --remove-orphans
# Wait for the web healthcheck (DB reachable), then check the full picture through Caddy.
for _ in $(seq 1 30); do
  [[ "$(docker inspect -f '{{.State.Health.Status}}' "$(compose ps -q web)")" == healthy ]] && break
  sleep 2
done
domain=$(grep -E '^DOMAIN=' .env | cut -d= -f2)
code=$(curl -s -o /dev/null -w '%{http_code}' "https://$domain/healthz/full/")
log "healthz/full: $code"
[[ "$code" == 200 ]] || { compose ps; exit 1; }
docker image prune -f >/dev/null
log "done"
