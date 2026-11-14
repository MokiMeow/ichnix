"""Measure routing and CI evidence retrieval in a disposable, real HydraDB node."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
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


if __name__ == "__main__":
    with isolated_graph() as instance:
        print(json.dumps(evaluate(instance), indent=2))
