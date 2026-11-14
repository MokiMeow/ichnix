"""Compare a staging deployment manifest to the organization's approved settings."""
import argparse
import json
import hashlib
import hmac
import os
import re
import stat
import sys
import uuid
from pathlib import Path

from graph import Graph


def _bytes(path, limit):
    with Path(path).open("rb") as file:
        raw = file.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("file exceeds size limit")
    return raw


def _object(raw):
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def load(path):
    return _object(_bytes(path, 262144))


def _key():
    path = os.environ.get("ICHNIX_CI_HMAC_KEY_FILE")
    if not path:
        raise ValueError("configure a CI signing key")
    with open(path, "rb") as source:
        mode = os.fstat(source.fileno()).st_mode
        if not stat.S_ISREG(mode) or mode & 0o077:
            raise ValueError("CI signing key must be a private regular file")
        key = source.read(1025)
    if not 32 <= len(key) <= 1024:
        raise ValueError("CI signing key must contain 32..1024 bytes")
    return key


def sign(manifest, output):
    raw = _bytes(manifest, 262144)
    digest = hmac.digest(_key(), raw, "sha256").hex()
    with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:
        file.write(digest + "\n")


def manifest_input(manifest, signature=None):
    raw = _bytes(manifest, 262144)
    method = "local-file"
    if signature is not None or "ICHNIX_CI_HMAC_KEY_FILE" in os.environ:
        if not signature:
            raise ValueError("detached CI signature required")
        supplied = _bytes(signature, 66).strip()
        expected = hmac.digest(_key(), raw, "sha256").hex().encode("ascii")
        if not re.fullmatch(b"[0-9a-f]{64}", supplied) or not hmac.compare_digest(supplied, expected):
            raise ValueError("invalid CI signature")
        method = "hmac-sha256"
    return _object(raw), {"source_digest": hashlib.sha256(raw).hexdigest(), "source_method": method}


def checks(report, policy, manifest):
    if manifest.get('environment') != 'staging':
        raise ValueError("expected a staging deployment")
    if any(manifest.get(k) != report[k] for k in ('service', 'revision')):
        raise ValueError("manifest does not match the report service and revision")
    services = policy.get('services')
    if not isinstance(services, dict) or report['service'] not in services:
        raise ValueError("service outside organization policy")
    required = services[report['service']]
    actual = manifest.get('settings')
    if not isinstance(actual, dict) or not isinstance(required, dict) or not 0 < len(required) <= 64:
        raise ValueError("expected settings and 1 to 64 approved assertions")
    observations = []
    for key, expected in required.items():
        if not key.isidentifier() or len(key) > 64 or type(expected) not in (str, bool, int):
            raise ValueError("invalid policy assertion")
        result = 'inconclusive'
        if key in actual:
            result = 'passed' if type(actual[key]) is type(expected) and actual[key] == expected else 'failed'
        observations.append({'step': key, 'revision': report['revision'], 'result': result})
    return observations


def main():
    if sys.argv[1:2] == ["--sign"]:
        signer = argparse.ArgumentParser(description="Sign the exact bytes of a CI manifest")
        signer.add_argument("--manifest", required=True)
        signer.add_argument("--out", required=True)
        options = signer.parse_args(sys.argv[2:])
        sign(options.manifest, options.out)
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case_id')
    parser.add_argument('--policy', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--signature', help='detached lowercase hexadecimal HMAC-SHA256 signature')
    args = parser.parse_args()
    graph = Graph()
    report = graph.context(args.case_id)['report']
    manifest, provenance = manifest_input(args.manifest, args.signature)
    events = checks(report, load(args.policy), manifest)
    run_id = uuid.uuid4().hex
    for event in events:
        event.update(run_id=run_id, **provenance)
        graph.add_evidence(args.case_id, event, expected_service=report["service"],
                           expected_component=report["component"])
    print(json.dumps(events, indent=2))
    return int(any(event['result'] != 'passed' for event in events))


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (KeyError, ValueError, RuntimeError, OSError) as error:
        print(f"staging check unavailable ({type(error).__name__})", file=sys.stderr)
        raise SystemExit(2)
