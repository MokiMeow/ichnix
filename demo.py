"""Run a synthetic demo with its own temporary HydraDB and console credentials."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
from urllib.parse import urlsplit
from uuid import uuid4
from unittest.mock import patch

from eval_graph import isolated_graph
from server import ConsoleServer, Handler, ROOT
from staging import checks, load


class PublicDemoHandler(Handler):
    """Expose only the sample case routes, even if the operator API grows."""

    def authorized(self, edit=False):
        return not edit

    def dispatch(self, write):
        path = urlsplit(self.path).path
        if write:
            return self.send(405, {"error": "read-only demo"})
        if path == "/api/session":
            return self.send(200, {"public_demo": True})
        if path.startswith(("/api/", "/hooks/")) and path not in self.server.demo_routes:
            return self.send(404, {"error": "unknown demo route"})
        if path in self.server.demo_traces:
            return self.send(200, self.server.demo_traces[path], "image/svg+xml")
        return super().dispatch(False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--public-readonly", action="store_true", help="serve synthetic cases without a token")
    args = parser.parse_args()
    with isolated_graph() as graph:
        cases = json.loads((ROOT / "examples/cases.json").read_text())
        for case in cases:
            graph.import_report(case)
        graph.record_review("IX-104", "data-platform", True)
        graph.record_review("IX-104", "product-security", False)
        policy = load(ROOT / "examples/policy.json")
        manifest = load(ROOT / "examples/staging.json")
        for case in cases[:2]:
            run = uuid4().hex
            for evidence in checks(case, policy, manifest):
                graph.add_evidence(case["id"], dict(evidence, run_id=run))
        if args.public_readonly:
            import subprocess
            with ConsoleServer(("127.0.0.1", args.port), PublicDemoHandler) as http:
                http.demo_routes = {"/api/cases"}
                http.demo_traces = {}
                for case in cases:
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
