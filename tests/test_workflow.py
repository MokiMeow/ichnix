import unittest
from eval_graph import isolated_graph
from staging import checks


class WorkflowTests(unittest.TestCase):
    def test_evidence_stays_with_revision_and_retry_is_idempotent(self):
        with isolated_graph() as db:
            row = dict(id="case-a", service="ledger", component="store", revision="v1",
                       owner="triage", title="Review retention window")
            db.import_report(row)
            event = dict(step="retention", result="failed", revision="v1", run_id="run-a")
            db.add_evidence(row["id"], event)
            db.add_evidence(row["id"], event)
            self.assertEqual(len(db.context(row["id"])["evidence"]), 1)
            db.import_report(dict(row, id="case-b"))
            self.assertEqual(db.ranked_checks("case-b")[0]["step"], "retention")
            db.import_report(dict(row, revision="v2"))
            self.assertEqual(db.context(row["id"])["evidence"], [])
            with self.assertRaises(ValueError):
                db.add_evidence(row["id"], event)

    def test_service_remap_isolates_same_revision_evidence(self):
        with isolated_graph() as db:
            row = dict(id="case-remap", service="archive", component="records", revision="v1",
                       owner="triage", title="Review retention")
            event = dict(step="retention", revision="v1", result="passed")
            db.import_report(row)
            db.add_evidence(row["id"], event, expected_service="archive", expected_component="records")
            db.import_report(dict(row, service="billing"))
            self.assertEqual(db.context(row["id"])["evidence"], [])
            with self.assertRaises(ValueError):
                db.add_evidence(row["id"], event, expected_service="archive", expected_component="records")
            with self.assertRaises(ValueError):
                db.add_evidence(row["id"], event, expected_service="billing", expected_component="audit")
            with self.assertRaises(ValueError):
                db.import_report(dict(row, id="unsafe/report"))
            db.import_report(row)
            self.assertEqual(len(db.context(row["id"])["evidence"]), 1)

    def test_policy_requires_matching_scope_and_distinguishes_absence_and_false(self):
        report = dict(service="ledger", revision="v1")
        policy = {"services": {"ledger": {"limit": 1, "redaction": True}}}
        manifest = dict(report, environment="staging", settings={"limit": True})
        self.assertEqual([e["result"] for e in checks(report, policy, manifest)],
                         ["failed", "inconclusive"])
        with self.assertRaises(ValueError):
            checks(report, policy, dict(manifest, revision="v2"))
