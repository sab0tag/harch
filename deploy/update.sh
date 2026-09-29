#!/usr/bin/env bash
# S6-07: update production to origin/main in one step (docs/06_deploy.md, «Обновление»).
# On the server:  /opt/harch/deploy/update.sh
# From a laptop:  ssh deploy@<server> /opt/harch/deploy/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose -f compose.prod.yml "$@"; }
log() { echo "$(date '+%F %T') update: $*"; }

before=$(git rev-parse HEAD)
git pull --ff-only
log "code at $(git log --oneline -1)"
compose build
compose run --rm web python manage.py migrate --noinput
if compose config --services | grep -qx demo-web; then  # training copy (COMPOSE_PROFILES=demo)
  compose up -d demo-db
  compose run --rm demo-web python manage.py migrate --noinput
  # A brand-new training database gets its first day right away; afterwards the nightly reset keeps it fresh.
  if [[ "$(compose run --rm -T demo-web python manage.py shell -c 'from apps.accounts.models import User; print(User.objects.exists())' | tail -1)" != True ]]; then
    compose run --rm demo-web python manage.py reset_demo
  fi
fi
compose up -d --remove-orphans
# Caddy sees its Caddyfile through a single-file bind mount, which keeps the old file after git pull:
# recreate it when the file changed (a few seconds without HTTPS).
if ! git diff --quiet "$before" HEAD -- deploy/Caddyfile; then
  log "Caddyfile changed — recreating caddy"
  compose up -d --force-recreate caddy
fi
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
