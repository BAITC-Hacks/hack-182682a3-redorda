#!/usr/bin/env bash
# Local, interactive Docker Compose setup. No credentials are stored in Git.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT_DIR"

usage() {
  cat <<'EOF'
Usage: bash scripts/quickstart-compose.sh [participant-kit.zip]

With the official participant ZIP: verify and import the full dataset, then
create your own administrator account. Without it: start the UI and create an
account; the bundled four-CSV demo can be imported in the UI but cannot run
the simulator.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
if [[ $# -gt 1 ]]; then
  usage >&2
  exit 2
fi
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "Docker with Compose v2 is required." >&2
  exit 1
fi

archive=""
if [[ $# -eq 1 ]]; then
  if [[ ! -f "$1" ]]; then
    echo "Participant ZIP not found: $1" >&2
    exit 1
  fi
  archive="$(cd "$(dirname "$1")" && pwd -P)/$(basename "$1")"
fi

if [[ ! -e .env ]]; then
  # Python runs in Docker, so judges do not need host Python or OpenSSL.
  secrets="$(docker run --rm python:3.12-slim python -c 'import secrets; print(secrets.token_hex(24)); print(secrets.token_hex(32))')"
  db_password="${secrets%%$'\n'*}"
  django_secret="${secrets#*$'\n'}"
  umask 077
  awk -v db_password="$db_password" -v django_secret="$django_secret" '
    /^POSTGRES_PASSWORD=/ { print "POSTGRES_PASSWORD=" db_password; next }
    /^DJANGO_SECRET_KEY=/ { print "DJANGO_SECRET_KEY=" django_secret; next }
    /^REDORDA_REQUIRE_AUTH=/ { print "REDORDA_REQUIRE_AUTH=1"; next }
    /^REDORDA_RUN_EXECUTION_ENABLED=/ { print "REDORDA_RUN_EXECUTION_ENABLED=1"; next }
    /^PARTICIPANT_KIT_DIR=/ { print "PARTICIPANT_KIT_DIR=/workspace/data/participant-kit"; next }
    { print }
  ' .env.example > .env
  echo "Created private .env with generated database and Django secrets."
else
  echo "Using existing .env without changing it."
  if ! grep -Eq '^DJANGO_DEBUG=1$' .env ||
     ! grep -Eq '^REDORDA_REQUIRE_AUTH=1$' .env ||
     ! grep -Eq '^REDORDA_RUN_EXECUTION_ENABLED=1$' .env; then
    echo "For this local HTTP quickstart, set DJANGO_DEBUG=1, REDORDA_REQUIRE_AUTH=1 and REDORDA_RUN_EXECUTION_ENABLED=1 in .env." >&2
    exit 1
  fi
fi

docker compose config --quiet

if [[ -n "$archive" ]]; then
  mkdir -p data/participant-kit
  docker run --rm --user "$(id -u):$(id -g)" \
    --mount "type=bind,source=$ROOT_DIR/scripts/import_participant_kit.py,target=/tmp/import_participant_kit.py,readonly" \
    --mount "type=bind,source=$ROOT_DIR/data/participant-kit,target=/tmp/participant-kit" \
    --mount "type=bind,source=$archive,target=/tmp/participant-kit.zip,readonly" \
    python:3.12-slim python /tmp/import_participant_kit.py \
    /tmp/participant-kit.zip --destination /tmp/participant-kit
fi

docker compose up --build -d

if [[ -f data/participant-kit/customer_profile.csv ]]; then
  docker compose exec -T backend python backend/manage.py import_participant_data \
    --path /workspace/data/participant-kit
  docker compose exec -T backend python backend/manage.py check_agent
  echo "Full participant dataset is ready for baseline runs."
else
  echo "Full participant dataset is absent. The bundled four-CSV demo is available in the UI; it cannot run the simulator."
fi

echo "Open http://localhost:5173 after creating your account."
if [[ -t 0 && -t 1 ]]; then
  echo "Create your own administrator account (password is entered privately):"
  docker compose exec backend python backend/manage.py createsuperuser
else
  echo "Create your account interactively with: docker compose exec backend python backend/manage.py createsuperuser"
fi
