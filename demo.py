"""Run a synthetic demo with its own temporary HydraDB and console credentials."""
import argparse
import json
import os
from datetime import datetime
from pathlib import Path
import re
import secrets
import subprocess
import sys
from urllib.parse import unquote, urlsplit
from uuid import uuid4
from unittest.mock import patch

from eval_graph import evaluate, isolated_graph
from scripts.acceptance import CASE, HEADERS, SOURCE, run_local_workflow
from server import ConsoleServer, Handler, ROOT
from staging import checks, load, manifest_input


def public_receipt(path):
    """Only the documented acceptance observations can be shared by the demo."""
    with Path(path).open("rb") as stream:
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError("receipt too large")
    data = json.loads(raw)
    expected = {"source": SOURCE, "case_id": CASE, "scope": "local Ichnix acceptance deployment",
                "source_method": "hmac-sha256", "handoff_state": "complete",
                "observations": dict.fromkeys(HEADERS, "passed")}
    if (any(data.get(k) != v for k, v in expected.items()) or data.get("review_recorded") is not True
            or not re.fullmatch(r"[0-9a-f]{64}", data.get("manifest_sha256", ""))):
        raise ValueError("unexpected acceptance receipt")
    at = datetime.fromisoformat(data["observed_at"])
    if at.utcoffset() is None:
        raise ValueError("receipt timestamp requires UTC offset")
    return {**expected, "review_recorded": True, "observed_at": at.isoformat(),
            "manifest_sha256": data["manifest_sha256"]}


class PublicDemoHandler(Handler):
    """Expose only the sample case routes, even if the operator API grows."""

    def authorized(self, edit=False):
        return not edit

    def dispatch(self, write):
        path = unquote(urlsplit(self.path).path)
        if write:
            return self.send(405, {"error": "read-only demo"})
        if path == "/api/acceptance" and self.server.receipt:
            return self.send(200, self.server.receipt)
        if path == "/api/evaluation":
            return self.send(200, self.server.evaluation)
        if path == "/api/session":
            return self.send(200, {"public_demo": True, "disclosed_case": self.server.disclosed_case})
        if path.startswith(("/api/", "/hooks/")) and path not in self.server.demo_routes:
            return self.send(404, {"error": "unknown demo route"})
        if path in self.server.demo_traces:
            return self.send(200, self.server.demo_traces[path], "image/svg+xml")
        return super().dispatch(False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--public-readonly", action="store_true", help="serve synthetic cases without a token")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--with-disclosure", action="store_true", help="run public intake and serve its case and receipt")
    mode.add_argument("--acceptance-receipt", help="share a separately completed public acceptance receipt")
    args = parser.parse_args()
    if (args.with_disclosure or args.acceptance_receipt) and not args.public_readonly:
        parser.error("sharing a receipt requires --public-readonly")
    if args.public_readonly:
        with isolated_graph() as benchmark:
            evaluation = evaluate(benchmark)
    with isolated_graph() as graph:
        receipt = run_local_workflow() if args.with_disclosure else None
        cases = json.loads((ROOT / "examples/cases.json").read_text())
        for case in cases:
            graph.import_report(case)
        graph.record_review("IX-104", "data-platform", True, actor="sample-reviewer")
        graph.record_review("IX-104", "product-security", False, actor="sample-reviewer")
        policy = load(ROOT / "examples/policy.json")
        with patch.dict(os.environ):
            os.environ.pop("ICHNIX_CI_HMAC_KEY_FILE", None)
            manifest, provenance = manifest_input(ROOT / "examples/staging.json")
        for case in cases[:2]:
            run = uuid4().hex
            for evidence in checks(case, policy, manifest):
                graph.add_evidence(case["id"], dict(evidence, run_id=run, **provenance))
        if args.public_readonly:
            with ConsoleServer(("127.0.0.1", args.port), PublicDemoHandler) as http:
                http.receipt = public_receipt(args.acceptance_receipt) if args.acceptance_receipt else receipt
                http.disclosed_case = CASE if args.with_disclosure else None
                http.evaluation = evaluation
                http.demo_routes = {"/api/cases"}
                http.demo_traces = {}
                for case in graph.cases():
                    route = "/api/cases/" + case["id"]
                    http.demo_routes.add(route)
                    trace = route + "/trace.svg"
                    http.demo_routes.add(trace)
                    events = graph.context(case["id"])["evidence"]
                    latest = events[-1].get("run_id") if events else None
                    payload = [e for e in events if e.get("run_id") == latest]
                    http.demo_traces[trace] = subprocess.run(
                        ["node", str(ROOT / "trace.mjs")], input=json.dumps(payload).encode(),
                        capture_output=True, check=True, timeout=10,
                    ).stdout
                print(f"Read-only synthetic demo on 127.0.0.1:{http.server_port}", flush=True)
                http.serve_forever()
            return
        key = secrets.token_urlsafe(32)
        path = Path(os.environ["ICHNIX_STATE_DIR"]) / "review-token"
        path.write_text(key)
        path.chmod(0o600)
        with patch.dict(os.environ, ICHNIX_REVIEW_TOKEN=key, ICHNIX_WEBHOOK_SECRET="", ICHNIX_READ_TOKEN=""):
            with ConsoleServer(("127.0.0.1", args.port), Handler) as http:
                print(f"Demo: http://127.0.0.1:{http.server_port}/console\nToken file: {path}", flush=True)
                http.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
