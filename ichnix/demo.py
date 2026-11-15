"""Run a disposable preview with sample cases and optional public disclosure intake."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
from tempfile import TemporaryDirectory
from urllib.parse import unquote, urlsplit
from uuid import uuid4
from unittest.mock import patch

from ichnix.eval_graph import evaluate, isolated_graph
from ichnix.handoffs import Handoffs
from scripts.acceptance import CASE, run_local_workflow
from ichnix.server import ConsoleServer, Handler, ROOT, diagram_svg
from ichnix.staging import checks, load, manifest_input, sign


GUIDED_CASE = {
    "id": "IX-214", "title": "Audit retention after a staging configuration change",
    "service": "ledger", "component": "audit-store", "revision": "build-108", "owner": "product-security",
}


def seed_guided_case(graph, policy, baseline):
    """Build a replayable review using only the disposable demo graph and manifests."""
    case = GUIDED_CASE
    case_id = case["id"]
    graph.import_report(case)
    graph.record_review(case_id, "product-security", False, actor="sample-reviewer", expected_scope=case)
    graph.record_review(case_id, "data-platform", True, actor="sample-reviewer", expected_scope=case)

    corrected = dict(baseline, settings={**baseline["settings"], "retention_days": 30})
    baseline = dict(baseline, settings={**baseline["settings"], "redaction_enabled": False})
    with TemporaryDirectory() as folder:
        key = Path(folder) / "sample.key"
        with key.open("xb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(secrets.token_bytes(32))
        with patch.dict(os.environ, ICHNIX_CI_HMAC_KEY_FILE=str(key)):
            for run, settings in (("baseline", baseline), ("after-change", corrected)):
                manifest = Path(folder) / f"{run}.json"
                signature = manifest.with_suffix(".sig")
                manifest.write_text(json.dumps(settings), encoding="utf-8")
                sign(manifest, signature)
                observed, provenance = manifest_input(manifest, signature)
                # Two sample submissions cite the same original deployment export.
                reports = (case_id, "IX-104") if run == "baseline" else (case_id,)
                for evidence in checks(case, policy, observed):
                    for report_id in reports:
                        graph.add_evidence(report_id, dict(evidence, run_id=run, **provenance),
                                           expected_service=case["service"], expected_component=case["component"])

        # Exercise a real lease without claiming jobs from the optional disclosure replay.
        queue = Handoffs(Path(folder) / "sample-queue.sqlite")
        job = queue.enqueue(graph, case_id, "evidence-review", expected_scope=case)
        worker = "sample-evidence-worker"
        lease = queue.claim(worker)
        queue.case_context(graph, job["id"], worker, lease["token"])
        queue.finish(job["id"], worker, lease["token"], disposition="needs-evidence", graph=graph)


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
        if path in self.server.demo_facts:
            case_id = path.split("/")[-2]
            return self.send(200, self.server.demo_facts[path], headers=[
                ("Content-Disposition", f'attachment; filename="{case_id}-selfgraph.json"')])
        if path in self.server.demo_traces:
            svg, headers = self.server.demo_traces[path]
            return self.send(200, svg, "image/svg+xml", headers)
        return super().dispatch(False)


def _run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--public-readonly", action="store_true", help="serve the isolated preview without a token")
    parser.add_argument("--with-disclosure", action="store_true", help="run public intake and serve its case and receipt")
    args = parser.parse_args()
    if args.with_disclosure and not args.public_readonly:
        parser.error("--with-disclosure requires --public-readonly")
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
            seed_guided_case(graph, policy, manifest)
            with ConsoleServer(("127.0.0.1", args.port), PublicDemoHandler) as http:
                http.receipt = receipt
                http.disclosed_case = CASE if args.with_disclosure else None
                http.evaluation = evaluation
                http.demo_routes = {"/api/cases"}
                http.demo_traces = {}
                http.demo_facts = {}
                public_ids = {case["id"] for case in cases}
                public_ids.add(GUIDED_CASE["id"])
                if http.disclosed_case:
                    public_ids.add(http.disclosed_case)
                for case in graph.cases():
                    if case["id"] not in public_ids:
                        raise RuntimeError("unexpected case in disposable public graph")
                    route = "/api/cases/" + case["id"]
                    http.demo_routes.add(route)
                    facts_route = route + "/selfgraph.json"
                    http.demo_routes.add(facts_route)
                    http.demo_facts[facts_route] = graph.selfgraph_facts(case["id"])
                    trace = route + "/trace.svg"
                    http.demo_routes.add(trace)
                    http.demo_traces[trace] = diagram_svg(graph.evidence_trace(case["id"])), []
                    diagram = route + "/diagram.svg"
                    http.demo_routes.add(diagram)
                    plan, source = graph.review_diagram(case["id"])
                    http.demo_traces[diagram] = diagram_svg(plan), [("X-Ichnix-Layout-Source", source)]
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


def main():
    try:
        _run()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
