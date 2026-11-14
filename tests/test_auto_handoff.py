import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from eval_graph import isolated_graph
from handoffs import Handoffs


CASE = {"id": "auto-case", "title": "Log retention review", "service": "archive",
        "revision": "stage-v2", "component": "retention", "owner": "records"}


class AutomaticHandoffTests(unittest.TestCase):
    def test_defaults_and_invalid_config_do_not_queue(self):
        with isolated_graph() as graph, patch.dict(os.environ):
            os.environ.pop("ICHNIX_AUTO_HANDOFFS", None)
            graph.import_report(CASE)
            os.environ["ICHNIX_AUTO_HANDOFFS"] = "yes"
            with self.assertRaises(ValueError):
                graph.import_report(dict(CASE, id="invalid"))
            self.assertEqual([row["id"] for row in graph.cases()], [CASE["id"]])
            self.assertIsNone(Handoffs().claim("agent"))
            with self.assertRaisesRegex(ValueError, "mapping changed"):
                Handoffs().enqueue(graph, CASE["id"], "owner-review", expected_scope=dict(CASE, service="other"))
            self.assertIsNone(Handoffs().claim("agent"))

    def test_retry_after_queue_failure_with_real_cli_and_graph(self):
        with isolated_graph() as graph, patch.dict(os.environ, ICHNIX_AUTO_HANDOFFS="1"):
            work = Path(os.environ["ICHNIX_STATE_DIR"])
            cli = [sys.executable, str(Path(__file__).resolve().parents[1] / "cli.py")]
            case = work / "case.json"
            case.write_text(json.dumps(CASE))
            policy, manifest = work / "policy.json", work / "manifest.json"
            policy.write_text(json.dumps({"services": {"archive": {"retention": 30, "mask": True}}}))
            manifest.write_text(json.dumps({"environment": "staging", "service": "archive",
                                            "revision": "stage-v2", "settings": {"retention": 15}}))
            queue_folder = work / "handoffs"
            queue_folder.touch()
            ingest = [*cli, "import", str(case)]
            self.assertNotEqual(subprocess.run(ingest, capture_output=True).returncode, 0)
            self.assertEqual(graph.cases(), [CASE])
            queue_folder.unlink()
            for _ in range(2):
                subprocess.run(ingest, check=True, capture_output=True)
            queue = Handoffs()
            job = queue.claim("worker")
            self.assertEqual((job["case_id"], job["kind"]), (CASE["id"], "owner-review"))
            queue.finish(job["id"], "worker", job["token"])
            self.assertIsNone(queue.claim("worker"))

            # Storage is unavailable after graph writes, then restored before replay.
            moved = work / "saved-queue"
            queue_folder.rename(moved)
            queue_folder.touch()
            stage = [*cli, "staging", CASE["id"], "--manifest", str(manifest), "--policy", str(policy)]
            self.assertEqual(subprocess.run(stage, capture_output=True).returncode, 2)
            rows = graph.context(CASE["id"])["evidence"]
            self.assertEqual({r["result"] for r in rows}, {"failed", "inconclusive"})
            queue_folder.unlink()
            moved.rename(queue_folder)
            for _ in range(2):
                self.assertEqual(subprocess.run(stage, capture_output=True).returncode, 1)
            self.assertEqual(graph.context(CASE["id"])["evidence"], rows)
            job = queue.claim("worker")
            self.assertEqual(job["kind"], "evidence-review")
            self.assertEqual(queue.case_context(graph, job["id"], "worker", job["token"])["evidence"], rows)
            queue.finish(job["id"], "worker", job["token"])
            self.assertIsNone(queue.claim("worker"))

            manifest.write_text(json.dumps({"environment": "staging", "service": "archive", "revision": "stage-v2",
                                            "settings": {"retention": 30, "mask": True}}))
            self.assertEqual(subprocess.run(stage, capture_output=True).returncode, 0)
            next_job = queue.claim("worker")
            self.assertEqual(next_job["kind"], "evidence-review")
            self.assertNotEqual(next_job["id"], job["id"])
            self.assertEqual(len(graph.context(CASE["id"])["evidence"]), 4)
