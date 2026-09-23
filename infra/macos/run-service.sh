#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
set -a
source "$ROOT/.env.production"
set +a
cd "$ROOT"
case "${1:-}" in
  api)
    exec "$ROOT/.venv/bin/gunicorn" --chdir backend config.wsgi:application \
      --bind 127.0.0.1:8013 --workers 2 --timeout 360 --access-logfile - --error-logfile - ;;
  worker)
    exec "$ROOT/.venv/bin/celery" --workdir backend -A config worker \
      --loglevel=info --pool=prefork --concurrency=1 ;;
  web)
    exec /opt/homebrew/bin/node "$ROOT/infra/macos/proxy.mjs" ;;
  *) echo 'Usage: run-service.sh api|worker|web' >&2; exit 2 ;;
esac
