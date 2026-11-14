from contextlib import ExitStack
from http.client import HTTPConnection
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch

from credentials import issue
from eval_graph import isolated_graph
from server import ConsoleServer, Handler


class ReportUploadTests(unittest.TestCase):
    def test_named_intake_uploads_only_mapped_metadata(self):
        with isolated_graph() as graph, TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            registry = root / "identities.sqlite"
            stack.enter_context(patch.dict(os.environ, ICHNIX_IDENTITY_DB=str(registry)))
            tokens = {}
            for role in ("intake", "read", "review", "worker"):
                token_file = root / role
                issue(registry, role, role, token_file)
                tokens[role] = token_file.read_text().strip()
            server = stack.enter_context(ConsoleServer(("127.0.0.1", 0), Handler))
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            stack.callback(thread.join)
            stack.callback(server.shutdown)

            def request(role, route, data=None):
                conn = HTTPConnection("127.0.0.1", server.server_port, timeout=10)
                try:
                    headers = {"Content-Type": "application/json"}
                    if role:
                        headers["Authorization"] = "Bearer " + tokens[role]
                    conn.request("GET" if data is None else "POST", route,
                                 None if data is None else json.dumps(data), headers)
                    response = conn.getresponse()
                    return response.status, json.load(response)
                finally:
                    conn.close()

            route = "/api/import/report"
            record = dict(id="upload-1", title="Retention review", service="ledger",
                          revision="r7", component="archive", owner="records")
            for role in (None, "read", "worker", "review"):
                self.assertIn(request(role, route, record)[0], (401, 403))
            for invalid in ([record], {**record, "body": "private text"}, {"id": "incomplete"}):
                self.assertEqual(request("intake", route, invalid)[0], 400)
            self.assertEqual(graph.cases(), [])
            self.assertTrue(request("intake", "/api/session")[1]["can_import"])
            self.assertFalse(request("review", "/api/session")[1]["can_import"])
            for _ in range(2):
                self.assertEqual(request("intake", route, record), (200, {"id": "upload-1"}))
            with patch.dict(os.environ, ICHNIX_ORIGIN_SECRET="test-origin-credential-that-is-long-enough"):
                self.assertEqual(request("intake", route, record)[0], 403)
                conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                try:
                    conn.request("GET", "/api/session", headers={
                        "X-Ichnix-Origin-Secret": os.environ["ICHNIX_ORIGIN_SECRET"],
                        "Authorization": "Bearer " + tokens["intake"],
                    })
                    response = conn.getresponse()
                    self.assertTrue(json.load(response)["can_import"])
                finally:
                    conn.close()
            self.assertEqual(len(graph.cases()), 1)
            self.assertEqual(request("intake", "/api/cases/upload-1")[1]["report"], record)
            self.assertEqual(request("intake", "/api/cases/upload-1/reviews", {"accepted": True})[0], 401)
