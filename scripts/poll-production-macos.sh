#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export GIT_TERMINAL_PROMPT=0
cd "$ROOT"
remote_revision="$(git ls-remote --exit-code origin refs/heads/main | cut -f1)"
test -n "$remote_revision"
deployed_revision="$(cat logs/deployed-revision 2>/dev/null || true)"
if [ "$remote_revision" != "$deployed_revision" ]; then
  exec /bin/bash "$ROOT/scripts/deploy-production-macos.sh"
fi
