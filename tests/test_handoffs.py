from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ichnix.eval_graph import isolated_graph
from ichnix.handoffs import Handoffs


class GraphFixture:
    def snapshot_context(self, case, *, include_operator):
        return 1, {"report": {"id": case, "service": "ledger", "component": "export", "revision": "v1"},
                "recommendations": [{"owner": "accounts", "score": 0.8}], "evidence": []}


class HandoffTests(unittest.TestCase):
    def test_worker_reads_only_lease_fields_and_preserves_the_review_basis(self):
        case = {"id": "work", "title": "Retention", "service": "ledger", "component": "archive",
                "revision": "r2", "owner": "records"}
        with isolated_graph() as graph:
            graph.import_report(case)
            graph.import_report({**case, "id": "neighbor"})
            graph.add_evidence("work", {"step": "retention", "result": "passed", "revision": "r2"})
            graph.record_review("work", "records", True, actor="fixture")
            operator = graph.context("work")
            self.assertTrue(operator["review_history"] and operator["related_reports"])
            allowed = {k: operator[k] for k in ("report", "evidence", "recommendations", "ranked_checks")}
            query = graph._query
            def worker_query(statement, **params):
                if statement.startswith("MATCH"):
                    for other in ("[:AUDIT]", "[:WORKER_OUTCOME]", "r.title AS title, r.title_vector_v1 AS vector"):
                        self.assertNotIn(other, statement)
                return query(statement, **params)

            queue = Handoffs()
            with patch.object(graph, "_query", side_effect=worker_query):
                job = queue.enqueue(graph, case["id"], "evidence-review")
                lease = queue.claim("worker")
                self.assertEqual(queue.case_context(graph, job["id"], "worker", lease["token"]), allowed)
                result = queue.finish(job["id"], "worker", lease["token"], disposition="evidence-reviewed", graph=graph)
                self.assertEqual(result["state"], "complete")

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
