import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from ichnix.eval_graph import isolated_graph

CLI = Path(__file__).resolve().parents[1] / "cli.py"


class SelfGraphExportTests(unittest.TestCase):
    def test_snapshot_scope_repeatability_and_private_output(self):
        case = dict(id="SG-10", title="excluded report title", owner="excluded-owner",
                    service="ledger", component="archive", revision="r1")
        with isolated_graph() as graph, TemporaryDirectory() as tmp:
            def export(target):
                return subprocess.run([sys.executable, CLI, "export-selfgraph", case["id"], "--out", target],
                                      capture_output=True, text=True, timeout=15)

            graph.import_report(case)
            graph.add_evidence(case["id"], dict(revision="r1", step="retention", result="failed", run_id="build-7",
                                                source_digest="a" * 64, source_method="hmac-sha256"))
            graph.add_evidence(case["id"], dict(revision="r1", step="retention", result="inconclusive", run_id="build-8"))
            output = Path(tmp) / "bundle.json"
            proc = export(output)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn(case["id"], proc.stdout + proc.stderr)
            self.assertEqual(json.loads(proc.stdout), {"format": "selfgraph-facts", "entities": 4, "relations": 3})
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            payload = output.read_text()
            self.assertNotIn(case["title"], payload)
            self.assertNotIn(case["owner"], payload)
            facts = json.loads(payload)
            self.assertEqual(facts, graph.selfgraph_facts(case["id"]))
            self.assertEqual(len({e["id"] for e in facts["entities"]}), 4)
            obs = [e["properties"] for e in facts["entities"] if e["type"] == "ControlObservation"]
            self.assertEqual({e["result"] for e in obs}, {"inconclusive", "failed"})
            self.assertEqual(next(e for e in obs if e["result"] == "failed")["source_digest"], "a" * 64)
            self.assertNotEqual(export(output).returncode, 0)
            self.assertEqual(output.read_text(), payload)
            graph.import_report(dict(case, revision="r2"))
            remapped = graph.selfgraph_facts(case["id"])
            self.assertEqual(len(remapped["relations"]), 1)
            self.assertTrue({e["id"] for e in facts["entities"]}.isdisjoint(e["id"] for e in remapped["entities"]))
