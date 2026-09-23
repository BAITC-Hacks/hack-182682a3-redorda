#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/Users/oa/.homebrew/opt/postgresql@16/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
mkdir -p "$ROOT/logs" "$ROOT/backups"
exec >> "$ROOT/logs/deploy.log" 2>&1
LOCKDIR="/tmp/oa-redorda-deploy.lock"
mkdir "$LOCKDIR" || { echo 'Deployment already running'; exit 1; }
trap 'rmdir "$LOCKDIR"' EXIT
cd "$ROOT"
echo "Deployment started: $(date -u)"
if [ "${1:-}" != '--local' ]; then
  test -z "$(git status --porcelain)" || { echo 'Working tree is dirty; refusing to overwrite changes'; exit 1; }
  git fetch origin main
  git merge --ff-only origin/main
  test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"
fi
test -f .env.production
if [ ! -x .venv/bin/python ]; then
  /opt/homebrew/bin/python3.13 -m venv .venv
fi
.venv/bin/python -m pip install -q -r requirements.txt
npm --prefix frontend ci --no-audit --no-fund
# Checks use a separate test database; production configuration is loaded afterwards.
make check
set -a
source .env.production
set +a
pg_dump --format=custom --file="$ROOT/backups/redorda-$(date '+%Y%m%d-%H%M%S').dump" "$DATABASE_URL"
.venv/bin/python backend/manage.py migrate --noinput
.venv/bin/python backend/manage.py collectstatic --noinput
.venv/bin/python backend/manage.py check
mkdir -p "$HOME/Library/LaunchAgents"
for component in api worker web; do
  label="com.oa.redorda.$component"
  plist="$HOME/Library/LaunchAgents/$label.plist"
  generated="$ROOT/logs/$label.plist"
  sed "s|__ROOT__|$ROOT|g" "$ROOT/infra/macos/$label.plist" > "$generated"
  plutil -lint "$generated"
  if launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
    if cmp -s "$generated" "$plist"; then
      launchctl kickstart -k "gui/$(id -u)/$label"
      continue
    fi
    launchctl bootout "gui/$(id -u)/$label"
  fi
  install -m 0644 "$generated" "$plist"
  launchctl bootstrap "gui/$(id -u)" "$plist"
done
for _ in {1..30}; do
  if curl -fsS --max-time 2 -H 'Host: hack.1ge.kz' http://127.0.0.1:8103/api/v1/health/; then break; fi
  sleep 1
done
curl -fsS --max-time 10 -H 'Host: hack.1ge.kz' http://127.0.0.1:8103/api/v1/health/
curl -fsS --max-time 10 http://127.0.0.1:8103/ > /dev/null
git rev-parse HEAD > "$ROOT/logs/deployed-revision"
echo "Deployment completed: $(date -u)"
