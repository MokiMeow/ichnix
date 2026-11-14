#!/bin/sh
# Run HydraDB v0.4.0 with a private disk store and loopback-only listeners.
set -eu
umask 077
state=${ICHNIX_STATE_DIR:-"${XDG_STATE_HOME:-$HOME/.local/state}/ichnix"}
mkdir -p "$state/store" "$state/cache"
export GRAPH_AUTH_TOKEN_FILE=${ICHNIX_HYDRA_TOKEN_FILE:-"$state/hydra-token"}
if [ ! -f "$GRAPH_AUTH_TOKEN_FILE" ]; then
    python3 - "$GRAPH_AUTH_TOKEN_FILE" <<'PY'
import os, secrets, sys
with os.fdopen(os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as token:
    token.write(secrets.token_urlsafe(48))
PY
fi
export CLOUD_PROVIDER=local LOCAL_PATH="$state/store" GRAPH_DATA_CACHE_DIR="$state/cache"
export GRAPH_NAMESPACE=${ICHNIX_HYDRA_NAMESPACE:-default} GRAPH_ID=${ICHNIX_HYDRA_GRAPH:-default}
export GRAPH_CELL_ID=${ICHNIX_HYDRA_CELL:-cell-0} GRAPH_NODE_ID=node-0
export GRAPH_CELLS="$GRAPH_CELL_ID" GRAPH_ALLOW_PLAINTEXT=true RUST_MIN_STACK=33554432
export GRAPH_BOLT_ADDR=127.0.0.1:${ICHNIX_BOLT_PORT:-7687}
export GRAPH_ADVERTISED_BOLT_ADDR="$GRAPH_BOLT_ADDR" GRAPH_BOLT_NODE_ADDRESSES="node-0=$GRAPH_BOLT_ADDR"
export GRAPH_HTTP_ADDR=127.0.0.1:${ICHNIX_QUERY_PORT:-8443}
export GRAPH_ADMIN_ADDR=127.0.0.1:${ICHNIX_ADMIN_PORT:-9090}
export GRAPH_USAGE_REPORT_ENABLED=false GRAPH_QUERY_TELEMETRY_ENABLED=false
if [ -z "${HYDRA_BINARY:-}" ]; then
    runtime="${XDG_CACHE_HOME:-$HOME/.cache}/ichnix-runtime"
    if [ -x "$runtime/graph-node" ]; then
        HYDRA_BINARY="$runtime/graph-node"
        export LD_LIBRARY_PATH="$runtime${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    else
        HYDRA_BINARY=graph-node
    fi
fi
exec "$HYDRA_BINARY"
