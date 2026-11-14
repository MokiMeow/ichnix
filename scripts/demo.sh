#!/bin/sh
# Load only synthetic cases into a dedicated demonstration graph.
set -eu
cd "$(dirname "$0")/.."
python3 cli.py import examples/cases.json
python3 cli.py review IX-104 data-platform --accept
python3 cli.py review IX-104 product-security --reject
set +e
python3 staging.py IX-104 --manifest examples/staging.json --policy examples/policy.json
status=$?
set -e
# The sample's retention period intentionally exceeds the configured policy.
[ "$status" = 1 ] || exit 2
python3 cli.py context IX-108
