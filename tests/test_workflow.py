import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from eval_graph import isolated_graph
from graph import Graph
from staging import checks


class WorkflowTests(unittest.TestCase):
    def test_configured_graph_credentials_work_without_a_home_directory(self):
        with TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            token_file = Path(temp) / "hydra-token"
            token_file.write_text("disposable-test-credential")
            for variable, value in (("ICHNIX_HYDRA_TOKEN_FILE", str(token_file)), ("ICHNIX_STATE_DIR", temp)):
                with self.subTest(setting=variable), patch.dict(os.environ, {variable: value}), \
                        patch.object(Path, "home", side_effect=RuntimeError("no home directory")):
                    self.assertEqual(Graph().headers["Authorization"], "Bearer disposable-test-credential")

    def test_survey_isolates_and_restores_operator_configuration(self):
        external = {"ICHNIX_HANDOFF_DB": "/unavailable/private/queue", "ICHNIX_IDENTITY_DB": "/unavailable/private/identities",
                    "HACKERONE_API_TOKEN": "fixture", "BUGCROWD_API_TOKEN": "fixture"}
        with patch.dict(os.environ, external):
            with isolated_graph() as db:
                self.assertTrue(external.keys().isdisjoint(os.environ))
                self.assertEqual(db.cases(), [])
            self.assertEqual({key: os.environ[key] for key in external}, external)

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
