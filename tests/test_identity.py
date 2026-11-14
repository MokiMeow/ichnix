from http.client import HTTPConnection
import json
import os
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch

from ichnix.credentials import identify, identities, issue, revoke
from ichnix.eval_graph import isolated_graph
from ichnix.server import ConsoleServer, Handler


class IdentityTests(unittest.TestCase):
    def test_identity_files_revocation_and_scope(self):
        with TemporaryDirectory() as tmp:
            db, file = Path(tmp) / "registry.sqlite", Path(tmp) / "token"
            with patch.dict(os.environ, ICHNIX_IDENTITY_DB=str(db), ICHNIX_REVIEW_TOKEN=secrets.token_hex()):
                self.assertIsNone(identify("Bearer " + os.environ["ICHNIX_REVIEW_TOKEN"]))
                issue(db, "auditor", "read", file)
                token = file.read_text().strip()
                self.assertEqual(file.stat().st_mode & 0o777, 0o600)
                self.assertNotIn(token.encode(), db.read_bytes())
                self.assertEqual(identify("Bearer " + token), "auditor")
                self.assertIsNone(identify("Bearer " + token, edit=True))
                self.assertNotIn(token, json.dumps(identities(db)))
                revoke(db, "auditor")
                self.assertIsNone(identify("Bearer " + token))


class ReviewAuditTests(unittest.TestCase):
    def test_http_review_retry_and_reversal_on_real_graph(self):
        with isolated_graph() as graph, TemporaryDirectory() as tmp:
            db = Path(tmp) / "registry.sqlite"
            with patch.dict(os.environ, ICHNIX_IDENTITY_DB=str(db)):
                reviewer, auditor = Path(tmp) / "a", Path(tmp) / "b"
                issue(db, "alice", "review", reviewer)
                issue(db, "bob", "read", auditor)
                graph.import_report({"id": "fixture-1", "title": "Owner review", "revision": "r2",
                                     "service": "shipping", "component": "labels", "owner": "ops"})
                server = ConsoleServer(("127.0.0.1", 0), Handler)
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()

                def query(token_file, method="GET", accepted=None, key=None):
                    con = HTTPConnection(*server.server_address, timeout=10)
                    route = "/api/cases/fixture-1" + ("/reviews" if method == "POST" else "")
                    headers = {"Authorization": "Bearer " + token_file.read_text().strip()}
                    if key:
                        headers["Idempotency-Key"] = key
                    body = json.dumps({"owner": "ops", "accepted": accepted}) if method == "POST" else None
                    try:
                        con.request(method, route, body, headers)
                        response = con.getresponse()
                        return response.status, json.load(response)
                    finally:
                        con.close()
                try:
                    self.assertEqual(query(auditor, "POST", True)[0], 401)
                    self.assertEqual(query(reviewer, "POST", True, "first")[0], 200)
                    self.assertEqual(query(reviewer, "POST", False, "second")[0], 200)
                    status, row = query(reviewer, "POST", True, "first")
                    self.assertEqual(status, 200)
                    self.assertEqual([(d["actor"], d["accepted"]) for d in row["review_history"]],
                                     [("alice", True), ("alice", False)])
                    self.assertLess(row["review_history"][0]["at"], row["review_history"][1]["at"])
                    self.assertEqual(row["recommendations"][0]["score"], 0.5)
                    self.assertEqual(query(reviewer, "POST", False, "first")[0], 400)
                    self.assertEqual(query(auditor)[0], 200)
                    revoke(db, "alice")
                    self.assertEqual(query(reviewer, "POST", True)[0], 401)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()


if __name__ == "__main__":
    unittest.main()
