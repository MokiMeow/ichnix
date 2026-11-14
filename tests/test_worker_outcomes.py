from pathlib import Path
import os
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

from credentials import issue
from eval_graph import isolated_graph
from graph import Graph
from handoffs import Handoffs
from server import ConsoleServer, Handler
from worker_client import Worker


CASE = {"id": "outcome-1", "service": "records", "component": "store", "revision": "r2",
        "title": "Existing records review", "owner": "archives"}


class WorkerOutcomeTests(unittest.TestCase):
    def test_private_tool_disposition_and_retries(self):
        with isolated_graph() as graph, tempfile.TemporaryDirectory() as folder:
            graph.import_report(CASE)
            graph.add_evidence(CASE["id"], {"step": "retention", "result": "failed", "revision": "r2"})
            before = graph.context(CASE["id"])
            job = Handoffs().enqueue(graph, CASE["id"], "evidence-review")
            db, token = Path(folder) / "ids.sqlite", Path(folder) / "worker.token"
            issue(db, "worker-1", "worker", token)
            with patch.dict(os.environ, ICHNIX_IDENTITY_DB=str(db)), ConsoleServer(("127.0.0.1", 0), Handler) as api:
                thread = Thread(target=api.serve_forever, daemon=True)
                thread.start()
                try:
                    tool = Worker(f"http://127.0.0.1:{api.server_port}", token).call
                    job_id = job["id"]
                    self.assertEqual(tool("claim_review", {})["id"], job_id)
                    tool("get_review_context", {"job_id": job_id})
                    with self.assertRaises(ValueError):
                        tool("finish_review", {"job_id": job_id, "disposition": "needs-owner-review"})
                    args = {"job_id": job_id, "disposition": "needs-evidence"}
                    result = tool("finish_review", args)
                    self.assertEqual((result["state"], result["disposition"]), ("complete", "needs-evidence"))
                    self.assertEqual(tool("finish_review", args), result)
                    with self.assertRaises(RuntimeError):
                        tool("finish_review", {**args, "disposition": "evidence-reviewed"})
                finally:
                    api.shutdown()
                    thread.join()
            after = graph.context(CASE["id"])
            self.assertEqual(before["recommendations"], after["recommendations"])
            event, = after["worker_outcomes"]
            self.assertEqual({k: event[k] for k in ("worker", "disposition", "job_id", "kind")},
                             dict(worker="worker-1", disposition="needs-evidence", job_id=job_id, kind="evidence-review"))
            self.assertTrue(event["at"])

    def test_requires_current_lease_read_and_replays_failed_graph_publication(self):
        with isolated_graph() as graph:
            graph.import_report(CASE)
            queue = Handoffs()
            job_id = queue.enqueue(graph, CASE["id"], "owner-review")["id"]
            with patch("handoffs.time.time", return_value=100):
                first = queue.claim("a", 1)
                queue.case_context(graph, job_id, "a", first["token"])
            new = queue.claim("b", 120)
            def finish(actor, proof):
                return queue.finish(job_id, actor, proof, disposition="needs-owner-review", graph=graph)
            for actor, proof in (("a", first["token"]), ("b", new["token"])):
                with self.assertRaises(PermissionError):
                    finish(actor, proof)
            queue.case_context(graph, job_id, "b", new["token"])
            with patch.object(graph, "publish_outcome", side_effect=RuntimeError("offline")):
                with self.assertRaises(RuntimeError):
                    finish("b", new["token"])
            self.assertEqual(queue.status(job_id)["state"], "publishing")
            self.assertEqual(graph.context(CASE["id"])["worker_outcomes"], [])
            self.assertIsNone(Handoffs().claim("next-agent", graph_factory=Graph))
            self.assertEqual(queue.status(job_id)["state"], "complete")
            self.assertEqual(finish("b", new["token"])["disposition"], "needs-owner-review")
            self.assertEqual(len(graph.context(CASE["id"])["worker_outcomes"]), 1)

    def test_refuses_changed_review_material_or_revision(self):
        with isolated_graph() as graph:
            graph.import_report(CASE)
            obs = {"step": "retention", "result": "failed", "revision": "r2"}
            graph.add_evidence(CASE["id"], obs)
            q = Handoffs()
            for change in ("observation", "mapping"):
                job_id = q.enqueue(graph, CASE["id"], "evidence-review")["id"]
                job = q.claim("w")
                q.case_context(graph, job_id, "w", job["token"])
                if change == "mapping":
                    graph.import_report({**CASE, "revision": "r3"})
                else:
                    graph.add_evidence(CASE["id"], {**obs, "result": "passed"})
                with self.assertRaises((ValueError, PermissionError)):
                    q.finish(job_id, "w", job["token"], disposition="evidence-reviewed", graph=graph)
                self.assertEqual(graph.context(CASE["id"])["worker_outcomes"], [])
                q.finish(job_id, "w", job["token"])
