#!/usr/bin/env bash
# CI-only recovery drill. Requires the disposable workspace initialized by verify.yml.
set -euo pipefail
umask 077
if [[ ${GITHUB_ACTIONS:-} != true || ! ${COMPOSE_PROJECT_NAME:-} =~ ^ichnix-ci-[0-9]+-[0-9]+$ ]]; then
  echo "expected the disposable CI Compose project" >&2
  exit 2
fi

source_project=$COMPOSE_PROJECT_NAME
restored_project="${source_project}-restore"
snapshots=$(mktemp -d "${RUNNER_TEMP:?}/ichnix-recovery.XXXXXXXX")
trap 'rm -rf -- "$snapshots"' EXIT
volumes=(graph_data graph_auth console_data)
source_cli=(docker compose -p "$source_project" exec -T console python3 cli.py)
restore=(docker compose -p "$restored_project")
helper=(docker run --rm --network none --read-only --security-opt no-new-privileges
        --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --user 0:0)

for name in "${volumes[@]}"; do
  vol="${source_project}_$name"
  label=$(docker volume inspect --format '{{index .Labels "com.docker.compose.project"}}' "$vol")
  [[ $label == "$source_project" ]]
  if docker volume inspect "${restored_project}_$name" >/dev/null 2>&1; then
    echo "restore requires unused destination volumes" >&2
    exit 1
  fi
done

# Persist synthetic worker and revoked-reader identities before taking the backup.
"${source_cli[@]}" identity issue ci-worker --role worker --out /var/lib/ichnix/ci-worker.token
"${source_cli[@]}" identity issue ci-revoked --role read --out /var/lib/ichnix/ci-revoked.token
"${source_cli[@]}" identity revoke ci-revoked
job=$("${source_cli[@]}" handoff enqueue IX-108 owner-review | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
[[ $job =~ ^[a-f0-9]{64}$ ]]
auth_hash=$(docker compose -p "$source_project" exec -T console sha256sum /run/ichnix-auth/hydra-token)
docker compose -p "$source_project" down

for name in "${volumes[@]}"; do
  vol="${source_project}_$name"
  # No live service may write while these filesystem snapshots are being taken.
  [[ -z $(docker ps --filter "volume=$vol" --quiet) ]]
  "${helper[@]}" --mount "type=volume,source=$vol,target=/volume,readonly,volume-nocopy" \
    ichnix:local tar --numeric-owner -C /volume -cf - . > "$snapshots/$name.tar"
done
(cd "$snapshots" && sha256sum ./*.tar > SHA256SUMS && sha256sum --check SHA256SUMS)

# Let Compose assign labels and ownership of these volumes; do not start either service yet.
"${restore[@]}" create --no-build
for name in "${volumes[@]}"; do
  vol="${restored_project}_$name"
  label=$(docker volume inspect --format '{{index .Labels "com.docker.compose.project"}}' "$vol")
  [[ $label == "$restored_project" ]]
  # shellcheck disable=SC2016 # Check the destination inside the helper container.
  "${helper[@]}" -i --mount "type=volume,source=$vol,target=/volume,volume-nocopy" \
    ichnix:local sh -ec '
      test -z "$(find /volume -mindepth 1 -print -quit)"
      tar --numeric-owner --same-owner -C /volume -xpf -
    ' < "$snapshots/$name.tar"
done
"${restore[@]}" up --detach --wait --no-build
[[ $("${restore[@]}" exec -T console sha256sum /run/ichnix-auth/hydra-token) == "$auth_hash" ]]
"${restore[@]}" exec -T console python3 cli.py storage check
"${restore[@]}" exec -T console python3 cli.py handoff status "$job" | python3 -c '
import json, sys
record = json.load(sys.stdin)
assert record["state"] == "queued" and record["case_id"] == "IX-108" and record["attempts"] == 0
'
"${restore[@]}" exec -T console python3 - "$job" <<'PY'
import json
from http.client import HTTPConnection
import os
from pathlib import Path
import sys

def request(identity, path, data=None, status=200, include_origin=True):
    token = (Path(os.environ["ICHNIX_STATE_DIR"]) / (identity + ".token")).read_text().strip()
    connection = HTTPConnection("127.0.0.1", int(os.getenv("ICHNIX_PORT", "8765")), timeout=10)
    try:
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        if include_origin:
            headers["X-Ichnix-Origin-Secret"] = os.environ["ICHNIX_ORIGIN_SECRET"]
        connection.request("GET" if data is None else "POST", path,
                           json.dumps(data) if data is not None else None, headers)
        response = connection.getresponse()
        assert response.status == status, (identity, path, response.status)
        return json.load(response)
    finally:
        connection.close()

case = "/api/cases/IX-108"
request("ci-reader", case, status=403, include_origin=False)
context = request("ci-reader", case)
assert context["report"]["revision"] == "build-108" and context["report"]["owner"] == "product-security"
request("ci-revoked", case, status=401)
job = request("ci-worker", "/api/handoffs/claim", {})
assert job["id"] == sys.argv[1] and job["kind"] == "owner-review" and job["attempts"] == 1
lease = "/api/handoffs/" + job["id"]
proof = {"token": job["token"]}
assert request("ci-worker", lease + "/context", proof)["report"] == context["report"]
assert request("ci-worker", lease + "/finish", proof)["state"] == "complete"
print("restored case, active reader, revoked reader, and queued worker lease verified")
PY
