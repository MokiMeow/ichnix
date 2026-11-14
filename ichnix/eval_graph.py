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

from ichnix.graph import Graph

# Same-revision recurring workflows with different services and disjoint report IDs.
# Held-out answers live only in the evaluator; no held-out feedback is ingested.
FIXTURES = (
    ("archive", "records", "retention_days"),
    ("catalog", "platform", "owner_record_present"),
    ("telemetry", "privacy", "mask_identifiers"),
)


@contextmanager
def isolated_graph():
    script = Path(__file__).resolve().parents[1] / "scripts/start-hydra.sh"
    with tempfile.TemporaryDirectory() as state:
        sockets = [socket.socket() for _ in range(3)]
        try:
            for listener in sockets:
                listener.bind(("127.0.0.1", 0))
            bolt, query, admin = [sock.getsockname()[1] for sock in sockets]
        finally:
            for listener in sockets:
                listener.close()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("ICHNIX_", "HACKERONE_", "BUGCROWD_", "GRAPH_"))}
        env.update(ICHNIX_AUTO_HANDOFFS="0", ICHNIX_STATE_DIR=state, ICHNIX_BOLT_PORT=str(bolt),
                   ICHNIX_QUERY_PORT=str(query), ICHNIX_ADMIN_PORT=str(admin),
                   ICHNIX_HYDRA_URL=f"http://127.0.0.1:{query}", ICHNIX_HYDRA_GRAPH="default",
                   ICHNIX_HYDRA_NAMESPACE="default", ICHNIX_HYDRA_CELL="cell-0",
                   ICHNIX_HYDRA_TOKEN_FILE=str(Path(state) / "token"))
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
    # A disjoint control cohort uses the same product code and feedback, but
    # pools every component under one revision key. Only the routing scope changes.
    def import_pair(case):
        pooled = dict(case, id="pooled-" + case["id"], component="pooled-control")
        return graph.import_report(case), graph.import_report(pooled)

    heldout = []
    for group, owner, check in FIXTURES:
        for service in ("billing", "reporting"):
            pair = import_pair(report(group, service, "holdout"))
            heldout.append((pair, owner, check))

    def accuracy(batch):
        scores = dict.fromkeys(("owner_top1", "ci_check_top1", "pooled_owner_top1",
                                "pooled_ci_check_top1", "catalog_owner_top1"), 0)
        for pair, expected_owner, expected_check in heldout:
            for prefix, key in zip(("", "pooled_"), pair):
                context = graph.context(key)
                scores[prefix + "owner_top1"] += context["recommendations"][0]["owner"] == expected_owner
                ranked = context["ranked_checks"]
                scores[prefix + "ci_check_top1"] += bool(ranked) and ranked[0]["step"] == expected_check
                if not prefix:
                    scores["catalog_owner_top1"] += context["report"]["owner"] == expected_owner
        return {"training_cases": batch, "heldout_cases": len(heldout),
                **{metric: correct / len(heldout) for metric, correct in scores.items()}}

    curve = [accuracy(0)]
    for index, (group, owner, check) in enumerate(FIXTURES, start=1):
        for service in ("ledger", "fulfillment"):
            case = report(group, service, "train")
            for key in import_pair(case):
                graph.record_review(key, owner, True, actor="benchmark-reviewer")
                graph.add_evidence(key, {"step": check, "revision": case["revision"], "result": "passed"})
        curve.append(accuracy(2 * index))
    return {"dataset": "synthetic, three component revisions; two held-out services each",
            "fixed_catalog_owner_top1": curve[0]["catalog_owner_top1"], "curve": curve}


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


def main():
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


if __name__ == "__main__":
    main()
