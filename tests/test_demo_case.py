import json
import unittest

from ichnix.demo import GUIDED_CASE, ROOT, seed_guided_case
from ichnix.eval_graph import isolated_graph
from ichnix.staging import checks, load


class GuidedCaseTests(unittest.TestCase):
    def test_two_runs_keep_the_unresolved_check_and_scoped_handoff(self):
        policy = load(ROOT / "examples/policy.json")
        baseline = load(ROOT / "examples/staging.json")
        with isolated_graph() as graph:
            prior = json.loads((ROOT / "examples/cases.json").read_text())[0]
            graph.import_report(prior)
            for evidence in checks(prior, policy, baseline):
                graph.add_evidence(prior["id"], dict(evidence, run_id="earlier-case"))
            case = GUIDED_CASE["id"]
            seed_guided_case(graph, policy, baseline)
            result = graph.context(case)
            observations = result["evidence"]
            self.assertEqual({(row["run_id"], row["step"]): row["result"] for row in observations}, {
                ("baseline", "retention_days"): "failed", ("after-change", "retention_days"): "passed",
                ("baseline", "redaction_enabled"): "passed", ("after-change", "redaction_enabled"): "passed",
                ("baseline", "audit_enabled"): "inconclusive", ("after-change", "audit_enabled"): "inconclusive",
            })
            self.assertEqual({row["source_method"] for row in observations}, {"hmac-sha256"})
            self.assertEqual(len({row["source_digest"] for row in observations}), 2)
            self.assertEqual(graph.evidence_trace(case)["run"], "after-change")
            self.assertEqual(result["recommendations"][0]["owner"], "data-platform")
            self.assertEqual([(r["owner"], r["accepted"]) for r in result["review_history"]],
                             [("product-security", False), ("data-platform", True)])
            self.assertEqual(result["related_reports"][0]["id"], prior["id"])
            self.assertEqual(result["ranked_checks"][0]["step"], "retention_days")
            outcome, = result["worker_outcomes"]
            self.assertEqual((outcome["worker"], outcome["disposition"]), ("sample-evidence-worker", "needs-evidence"))
