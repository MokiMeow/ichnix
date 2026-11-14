"""Run a synthetic demo with its own temporary HydraDB and console credentials."""
import json
import os
from pathlib import Path
import secrets
import sys
from uuid import uuid4
from unittest.mock import patch

from eval_graph import isolated_graph
from server import ConsoleServer, Handler, ROOT
from staging import checks, load


def main():
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
        key = secrets.token_urlsafe(32)
        path = Path(os.environ["ICHNIX_STATE_DIR"]) / "review-token"
        path.write_text(key)
        path.chmod(0o600)
        with patch.dict(os.environ, ICHNIX_REVIEW_TOKEN=key, ICHNIX_WEBHOOK_SECRET="", ICHNIX_READ_TOKEN=""):
            with ConsoleServer(("127.0.0.1", 0), Handler) as http:
                print(f"Demo: http://127.0.0.1:{http.server_port}/console\nToken file: {path}", flush=True)
                http.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
