"""Compare a staging deployment manifest to the organization's approved settings."""
import argparse
import json
import sys
import uuid
from pathlib import Path

from graph import Graph


def load(path):
    with Path(path).open('rb') as file:
        raw = file.read(262145)
    if len(raw) > 262144:
        raise ValueError("manifest exceeds size limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case_id')
    parser.add_argument('--policy', required=True)
    parser.add_argument('--manifest', required=True)
    args = parser.parse_args()
    graph = Graph()
    report = graph.context(args.case_id)['report']
    events = checks(report, load(args.policy), load(args.manifest))
    run_id = uuid.uuid4().hex
    for event in events:
        event["run_id"] = run_id
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
