from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ichnix.handoffs import Handoffs


class GraphFixture:
    def context(self, case):
        return {"report": {"id": case, "service": "ledger", "component": "export", "revision": "v1"},
                "recommendations": [{"owner": "accounts", "score": 0.8}], "evidence": []}


class HandoffTests(unittest.TestCase):
    def test_duplicate_enqueue_exclusive_claim_and_expiry_fencing(self):
        with TemporaryDirectory() as directory:
            q = Handoffs(Path(directory) / "queue.sqlite3")
            with ThreadPoolExecutor(8) as pool:
                ids = list(pool.map(lambda _: q.enqueue(GraphFixture(), "sample", "owner-review")["id"], range(16)))
                self.assertEqual(len(set(ids)), 1)
                with patch("ichnix.handoffs.time.time", return_value=100):
                    claims = list(pool.map(lambda w: q.claim(str(w), 2), range(16)))
            first, = (c for c in claims if c)
            with patch("ichnix.handoffs.time.time", return_value=103):
                with self.assertRaises(PermissionError):
                    q.finish(first["id"], first["worker"], first["token"])
                next_claim = q.claim("reviewer", 60)
                self.assertEqual(next_claim["attempts"], 2)
                with self.assertRaises(PermissionError):
                    q.finish(first["id"], first["worker"], first["token"])
                result = q.finish(next_claim["id"], "reviewer", next_claim["token"])
                reopened = Handoffs(q.path)
                self.assertEqual(result, reopened.finish(next_claim["id"], "reviewer", next_claim["token"]))
                self.assertNotIn("token", reopened.status(ids[0]))
                self.assertIsNone(reopened.claim("next"))
            with self.assertRaises(ValueError):
                q.enqueue(GraphFixture(), "sample", "evidence-review")
