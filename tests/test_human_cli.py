import json
import os
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch

from ichnix.credentials import issue, revoke
from ichnix.eval_graph import isolated_graph
from ichnix.server import ConsoleServer, Handler
from test_cli_config import cli


class HumanClientTests(unittest.TestCase):
    def test_profiles_use_private_http_without_graph_access(self):
        with isolated_graph() as graph, TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "registry.sqlite3"
            case = dict(id="survey-human", title="Inventory review", service="publications",
                        component="journal", revision="v1", owner="editors")
            graph.import_report(case)
            with patch.dict(os.environ, ICHNIX_IDENTITY_DB=str(db)), ConsoleServer(("127.0.0.1", 0), Handler) as http:
                for role in ("read", "review", "worker", "intake"):
                    issue(db, role, role, root / role)
                thread = Thread(target=http.serve_forever, daemon=True)
                thread.start()
                def invoke(role, *args):
                    return cli("human", *args, env={"PATH": os.environ["PATH"],
                               "ICHNIX_WORKSPACE_URL": f"http://127.0.0.1:{http.server_port}",
                               "ICHNIX_USER_TOKEN_FILE": str(root / role), "ICHNIX_HYDRA_TOKEN_FILE": "/missing"})
                try:
                    for role in ("read", "review", "worker", "intake"):
                        result = cli("auth", "status", "--workspace-url", f"http://127.0.0.1:{http.server_port}",
                                     "--token-file", str(root / role))
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(json.loads(result.stdout), {"name": role, "role": role})
                        self.assertNotIn((root / role).read_text().strip(), result.stdout + result.stderr)
                    observation = dict(step="retention", result="failed", revision="v1", run_id="staging-1",
                                       source_method="hmac-sha256", source_digest="a" * 64)
                    graph.add_evidence(case["id"], observation)
                    self.assertNotIn("review_ref", json.loads(invoke("read", "context", case["id"]).stdout)["evidence"][0])
                    ref = json.loads(invoke("review", "context", case["id"]).stdout)["evidence"][0]["review_ref"]
                    args = ("assess", case["id"], ref, "confirmed", "--priority", "high")
                    for role in ("worker", "intake", "read"):
                        self.assertNotEqual(invoke(role, *args).returncode, 0)
                    for _ in range(2):
                        result = invoke("review", *args)
                        self.assertEqual(result.returncode, 0, result.stderr)
                    assessment, = graph.context(case["id"])["assessments"]
                    self.assertEqual((assessment["reviewer"], assessment["decision"], assessment["priority"]),
                                     ("review", "confirmed", "high"))
                    self.assertNotIn("reviewer", graph.snapshot_context(case["id"], include_operator=False)[1]["assessments"][0])
                    self.assertNotEqual(invoke("review", "assess", case["id"], ref, "dismissed",
                                               "--priority", "high").returncode, 0)
                    self.assertEqual(invoke("review", "assess", case["id"], ref, "needs-evidence").returncode, 0)
                    updated, = graph.context(case["id"])["assessments"]
                    self.assertEqual((updated["decision"], updated["priority"]), ("needs-evidence", ""))
                    graph.import_report(dict(case, id="other"))
                    self.assertNotEqual(invoke("review", "assess", "other", ref, "dismissed").returncode, 0)
                    graph.import_report(dict(case, revision="next"))
                    self.assertNotEqual(invoke("review", *args).returncode, 0)
                    self.assertNotIn("assessments", graph.context(case["id"]))
                    graph.import_report(case)
                    upload = root / "case.json"
                    mapped = dict(case, id="intake-case")
                    upload.write_text(json.dumps(mapped))
                    self.assertNotEqual(invoke("review", "import-report", str(upload)).returncode, 0)
                    result = invoke("intake", "import-report", str(upload))
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(invoke("read", "context", mapped["id"]).stdout)["report"], mapped)
                    for role, can_review in (("read", False), ("review", True)):
                        con = HTTPConnection(*http.server_address, timeout=5)
                        try:
                            con.request("GET", "/api/session", headers={"Authorization": "Bearer " + (root / role).read_text().strip()})
                            session = con.getresponse()
                            self.assertEqual(session.status, 200)
                            self.assertIs(json.load(session)["can_review"], can_review)
                        finally:
                            con.close()
                    self.assertEqual(invoke("read", "cases").returncode, 0)
                    self.assertEqual(json.loads(invoke("read", "context", case["id"]).stdout)["report"], case)
                    self.assertNotEqual(invoke("read", "review", case["id"], case["owner"], "--accept").returncode, 0)
                    for _ in range(2):
                        result = invoke("review", "review", case["id"], case["owner"], "--accept", "--request-id", "review-retry")
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertNotIn((root / "review").read_text().strip(), result.stdout + result.stderr)
                    history = graph.context(case["id"])["review_history"]
                    self.assertEqual([(r["actor"], r["accepted"]) for r in history], [("review", True)])
                    self.assertNotEqual(invoke("worker", "cases").returncode, 0)
                    revoke(db, "read")
                    self.assertNotEqual(invoke("read", "cases").returncode, 0)
                finally:
                    http.shutdown()
                    thread.join()
