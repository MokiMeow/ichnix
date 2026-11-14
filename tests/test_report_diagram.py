from contextlib import contextmanager
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from ichnix.credentials import issue
from ichnix.eval_graph import isolated_graph
from ichnix.graph import Graph
from ichnix import review_layout

from ichnix.server import ConsoleServer, Handler


REPORT = {"id": "h1-example-930", "title": "Sensitive Company private retention review",
          "service": "payments", "component": "archive", "owner": "records-team", "revision": "r7"}
AI_ENV = {"ICHNIX_CF_ACCOUNT_ID": "a" * 32, "ICHNIX_CF_AI_TOKEN": "synthetic" * 8}


@contextmanager
def web(handler=Handler):
    with ConsoleServer(("127.0.0.1", 0), handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join()


class ReportDiagramTests(unittest.TestCase):
    def test_provider_minimized_and_validated_before_persistence(self):
        with isolated_graph() as graph, patch.dict(os.environ, AI_ENV):
            responses = [{"success": True, "result": {"response": {"layout": "owner-first"}}},
                         {"success": True, "result": {"choices": [{"message": {"content": '{"layout":"scope-first"}'}}]}}]
            with patch("ichnix.review_layout._post", side_effect=responses) as post:
                graph.import_report(REPORT)
                graph.import_report(REPORT)
                plan, source = Graph().review_diagram(REPORT["id"])
                self.assertEqual((source, plan["layout"]), ("workers-ai", "owner-first"))
                self.assertEqual(post.call_count, 1)
                args = post.call_args.args
                self.assertEqual(args[0:2], ("api.cloudflare.com", 443))
                body = args[-1]
                self.assertEqual(json.loads(body["messages"][1]["content"]), {"source": "hackerone", "topic": "data-handling"})
                for private in REPORT.values():
                    self.assertNotIn(private, json.dumps(body))
                graph.import_report({**REPORT, "title": "Permission review"})
                plan, source = graph.review_diagram(REPORT["id"])
                self.assertEqual((source, plan["layout"]), ("workers-ai", "scope-first"))
                self.assertEqual(post.call_count, 2)

    def test_fallback_after_invalid_plan_and_bounded_retry(self):
        with isolated_graph() as graph, patch.dict(os.environ, AI_ENV), patch(
                "ichnix.review_layout._post", return_value={"success": True, "result": {"response":
                    {"layout": "scope-first", "extra": "untrusted output"}}}) as post:
            with patch("ichnix.graph.time", return_value=1000):
                graph.import_report(REPORT)
                graph.import_report(REPORT)
                plan, source = graph.review_diagram(REPORT["id"])
                self.assertEqual((source, plan["layout"]), ("local", "evidence-first"))
                self.assertEqual(post.call_count, 1)
            with patch("ichnix.graph.time", return_value=1400):
                graph.import_report(REPORT)
            with patch("ichnix.graph.time", return_value=1900):
                graph.import_report(REPORT)
            self.assertEqual(post.call_count, 2)
            facts = review_layout.classify(REPORT)
            with patch.dict(os.environ, ICHNIX_LAYOUT_URL="https://org.example/api/report-layout",
                            ICHNIX_LAYOUT_SECRET="fixture" * 8), patch("ichnix.review_layout._post",
                            return_value={"layout": "owner-first"}) as relay:
                self.assertEqual(review_layout.select_layout(facts, "relay"), ("owner-first", "relay"))
                self.assertEqual(relay.call_args.args[-1], facts)

    def test_private_svg_is_available_before_evidence_and_reflects_latest_run(self):
        with isolated_graph() as graph, TemporaryDirectory() as root, web() as server:
            graph.import_report(REPORT)
            registry = Path(root) / "registry.sqlite"
            url = f"http://127.0.0.1:{server.server_port}/api/cases/{REPORT['id']}/diagram.svg"
            for name in ("read", "review", "worker"):
                issue(registry, name, name, Path(root) / name)
            def request(role):
                token = (Path(root) / role).read_text().strip()
                return urlopen(Request(url, headers={"Authorization": "Bearer " + token}), timeout=10)
            with patch.dict(os.environ, ICHNIX_IDENTITY_DB=str(registry)):
                with request("read") as response:
                    self.assertEqual(response.headers["X-Ichnix-Layout-Source"], "local")
                    self.assertEqual(response.headers.get_content_type(), "image/svg+xml")
                    text = " ".join(ET.fromstring(response.read()).itertext())
                    for item in ("Await staging evidence", REPORT["id"], REPORT["owner"], REPORT["component"]):
                        self.assertIn(item, text)
                with self.assertRaises(HTTPError) as denied:
                    request("worker")
                self.assertEqual(denied.exception.code, 401)
                denied.exception.close()
                for run, result in (("before", "failed"), ("after", "passed")):
                    graph.add_evidence(REPORT["id"], {"step": "retention", "result": result,
                                       "revision": REPORT["revision"], "run_id": run})
                with request("review") as response:
                    text = " ".join(ET.fromstring(response.read()).itertext())
                    self.assertIn("1 passed / 0 failed / 0 inconclusive", text)
                    self.assertNotIn("Await staging evidence", text)
