from http.client import HTTPConnection
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch

from ichnix.credentials import history, identify, identities, initialize, issue, revoke
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

    def test_cli_lifecycle_retry_and_private_history(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            db = folder / "registry.sqlite"
            with patch.dict(os.environ, ICHNIX_IDENTITY_DB=str(db)):
                issue(db, "alice", "read", folder / "first")
                first = (folder / "first").read_text().strip()
                with self.assertRaises(ValueError):
                    issue(db, "alice", "worker", folder / "unused")
                revoke(db, "alice")
                with self.assertRaises(ValueError):
                    revoke(db, "alice")
                with self.assertRaises(FileExistsError):
                    issue(db, "alice", "review", folder / "first")
                issue(db, "bob", "worker", folder / "bob")
                issue(db, "alice", "review", folder / "second")
                self.assertIsNone(identify("Bearer " + first))
                self.assertEqual(identify("Bearer " + (folder / "second").read_text().strip(), edit=True), "alice")
                cli = Path(__file__).resolve().parents[1] / "cli.py"
                raw = subprocess.check_output([sys.executable, cli, "identity", "history", "alice"])
                events = json.loads(raw)
                self.assertEqual([(e["event"], e["role"]) for e in events],
                                 [("issued", "read"), ("revoked", "read"), ("reissued", "review")])
                self.assertTrue(all(set(e) == {"at", "name", "event", "role", "actor"} and e["name"] == "alice"
                                    and e["actor"] == f"uid:{os.geteuid()}" for e in events))
                self.assertEqual(len(history(db)), 4)
                self.assertEqual(history(db, "unknown"), [])
                self.assertNotIn(first.encode(), raw)
                self.assertEqual([e["at"] for e in events], sorted(e["at"] for e in events))

    def test_legacy_registry_read_migration_and_transaction_rollback(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            db = folder / "registry.sqlite"
            db.touch(mode=0o600)
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE identities(name TEXT PRIMARY KEY, role TEXT NOT NULL, "
                             "digest TEXT NOT NULL UNIQUE, issued TEXT NOT NULL, revoked TEXT)")
                conn.executemany("INSERT INTO identities VALUES(?,?,?,?,?)",
                                 [("alice", "read", "a" * 64, "2024-01-01", None),
                                  ("bob", "worker", "b" * 64, "2024-02-01", "2024-03-01")])
                conn.commit()
                legacy = history(db)
                self.assertEqual([(e["name"], e["event"], e["actor"]) for e in legacy],
                                 [("alice", "issued", None), ("bob", "issued", None), ("bob", "revoked", None)])
                self.assertEqual(conn.execute("SELECT name FROM sqlite_schema WHERE type='table'").fetchall(),
                                 [("identities",)])
            revoke(db, "alice")
            initialize(db)
            self.assertEqual(history(db)[:-1], legacy)
            issue(db, "bob", "read", folder / "token")
            self.assertEqual(history(db, "bob")[-1]["event"], "reissued")
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TRIGGER blocked BEFORE INSERT ON identity_events "
                             "BEGIN SELECT RAISE(ABORT, 'blocked'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                revoke(db, "bob")
            self.assertIsNone(next(x for x in identities(db) if x["name"] == "bob")["revoked"])
            with self.assertRaises(sqlite3.IntegrityError):
                issue(db, "carl", "review", folder / "carl")
            self.assertFalse((folder / "carl").exists())
            self.assertEqual(history(db, "carl"), [])


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
