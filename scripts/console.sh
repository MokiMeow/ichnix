#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/ichnix"
mkdir -p "$state_dir"
if [[ ! -s "$state_dir/console-token" ]]; then
  python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$state_dir/console-token"
fi
export ICHNIX_REVIEW_TOKEN
ICHNIX_REVIEW_TOKEN=$(<"$state_dir/console-token")
printf 'Ichnix at http://127.0.0.1:%s/console\nReview token file: %s/console-token\n' "${ICHNIX_PORT:-8765}" "$state_dir"
exec python3 server.py --port "${ICHNIX_PORT:-8765}"
