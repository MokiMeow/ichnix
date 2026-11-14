"""Measure routing and CI evidence retrieval in a disposable, real HydraDB node."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import math
import socket
import subprocess
import tempfile
import time
from urllib.request import urlopen
from unittest.mock import patch

from graph import Graph

# Same-revision recurring workflows with different services and disjoint report IDs.
# Held-out answers live only in the evaluator; no held-out feedback is ingested.
FIXTURES = (
    ("archive", "records", "retention_days"),
    ("catalog", "platform", "owner_record_present"),
    ("telemetry", "privacy", "mask_identifiers"),
)


@contextmanager
def isolated_graph():
    script = Path(__file__).resolve().parent / "scripts/start-hydra.sh"
    with tempfile.TemporaryDirectory() as state:
        sockets = [socket.socket() for _ in range(3)]
        try:
            for listener in sockets:
                listener.bind(("127.0.0.1", 0))
            bolt, query, admin = [sock.getsockname()[1] for sock in sockets]
        finally:
            for listener in sockets:
                listener.close()
        env = dict(os.environ, ICHNIX_STATE_DIR=state, ICHNIX_BOLT_PORT=str(bolt),
                   ICHNIX_QUERY_PORT=str(query), ICHNIX_ADMIN_PORT=str(admin),
                   ICHNIX_HYDRA_URL=f"http://127.0.0.1:{query}", ICHNIX_HYDRA_GRAPH="default",
                   ICHNIX_HYDRA_NAMESPACE="default", ICHNIX_HYDRA_CELL="cell-0",
                   ICHNIX_HYDRA_TOKEN_FILE=str(Path(state) / "token"))
        env.pop("ICHNIX_HYDRA_TOKEN", None)
        with (Path(state) / "node.log").open("wb") as log:
            proc = subprocess.Popen([str(script)], env=env, stdout=log, stderr=log)
            try:
                for _ in range(100):
                    if proc.poll() is not None:
                        raise RuntimeError("disposable HydraDB exited during startup")
                    try:
                        with urlopen(f"http://127.0.0.1:{admin}/readyz", timeout=0.2):
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise RuntimeError("disposable HydraDB did not become ready")
                with patch.dict(os.environ, env, clear=True):
                    yield Graph()
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


def report(component, service, kind):
    return {"id": f"{kind}-{service}-{component}", "title": f"{service} evidence review",
            "service": service, "revision": "stage-v1", "component": component, "owner": "triage"}


def evaluate(graph):
    heldout = []
    for group, owner, check in FIXTURES:
        for service in ("billing", "reporting"):
            case = report(group, service, "holdout")
            graph.import_report(case)
            heldout.append((case["id"], owner, check))

    def accuracy(batch):
        owners = checks = 0
        for key, expected_owner, expected_check in heldout:
            context = graph.context(key)
            owners += context["recommendations"][0]["owner"] == expected_owner
            ranked = context["ranked_checks"]
            checks += bool(ranked) and ranked[0]["step"] == expected_check
        return {"training_cases": batch, "heldout_cases": len(heldout),
                "owner_top1": owners / len(heldout), "ci_check_top1": checks / len(heldout)}

    curve = [accuracy(0)]
    for index, (group, owner, check) in enumerate(FIXTURES, start=1):
        for service in ("ledger", "fulfillment"):
            case = report(group, service, "train")
            graph.import_report(case)
            graph.record_review(case["id"], owner, True)
            graph.add_evidence(case["id"], {"step": check, "revision": case["revision"], "result": "passed"})
        curve.append(accuracy(2 * index))
    return {"dataset": "synthetic, three component revisions; two held-out services each",
            "fixed_catalog_owner_top1": 0, "curve": curve}


def measure_reads(workers, requests):
    keys = [f"holdout-{service}-{component}" for component, _, _ in FIXTURES
            for service in ("billing", "reporting")]

    def read(index):
        start = time.perf_counter()
        context = Graph().context(keys[index % len(keys)])
        if not context["ranked_checks"]:
            raise ValueError("expected trained graph")
        return (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        durations = sorted(executor.map(read, range(requests)))
    return {"clients": workers, "context_requests": requests,
            "requests_per_second": round(requests / (time.perf_counter() - start), 1),
            "p50_ms": round(durations[math.ceil(requests * 0.50) - 1], 1),
            "p95_ms": round(durations[math.ceil(requests * 0.95) - 1], 1)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clients", type=int, default=0, help="also measure concurrent context retrieval")
    parser.add_argument("--requests", type=int, default=128)
    args = parser.parse_args()
    if not 0 <= args.clients <= 128 or not 1 <= args.requests <= 10000:
        parser.error("clients must be 0..128 and requests must be 1..10000")
    with isolated_graph() as instance:
        result = evaluate(instance)
        if args.clients:
            result["load"] = measure_reads(args.clients, args.requests)
        print(json.dumps(result, indent=2))
