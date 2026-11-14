#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
state_dir="${ICHNIX_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/ichnix}"
mkdir -p "$state_dir"
if [[ -n "${ICHNIX_IDENTITY_DB:-}" ]]; then
  printf 'Named identity registry: %s\n' "$ICHNIX_IDENTITY_DB"
elif [[ -z "${ICHNIX_REVIEW_TOKEN:-}" ]]; then
  token_file="$state_dir/console-token"
  if [[ ! -e "$token_file" ]]; then
    tmp_token=$(mktemp "$state_dir/.console-token.XXXXXXXX")
    trap 'rm -f -- "$tmp_token"' EXIT
    python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$tmp_token"
    ln -T -- "$tmp_token" "$token_file" || test -f "$token_file"
    rm -f -- "$tmp_token"
    trap - EXIT
  fi
  export ICHNIX_REVIEW_TOKEN
  ICHNIX_REVIEW_TOKEN=$(<"$token_file")
  printf 'Review token file: %s\n' "$token_file"
fi
printf 'Ichnix at http://127.0.0.1:%s/console\n' "${ICHNIX_PORT:-8765}"
exec python3 server.py --port "${ICHNIX_PORT:-8765}" "$@"
