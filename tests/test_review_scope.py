import os
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from ichnix.eval_graph import isolated_graph


class ReviewScopeTests(TestCase):
    def test_owner_reason_counts_review_decisions_separately_from_catalog(self):
        case = dict(id="one", title="Owner routing", service="archive", component="retention",
                    revision="build-7", owner="data")
        with isolated_graph() as db:
            db.import_report(case)
            db.record_review("one", "records", True)
            db.record_review("one", "data", False)
            reasons = {r["owner"]: r["reason"] for r in db.context("one")["recommendations"]}
            self.assertIn("1 accepted, 0 rejected", reasons["records"])
            self.assertIn("catalog assignment", reasons["data"])
            self.assertIn("0 accepted, 1 rejected", reasons["data"])
            db.import_report({**case, "revision": "build-8"})
            only, = db.context("one")["recommendations"]
            self.assertIn("catalog assignment", only["reason"])
            self.assertIn("no recorded owner reviews", only["reason"])

    def test_remapping_case_rejects_decision_from_an_older_console_view(self):
        case = dict(id="one", title="Owner routing", service="archive", component="retention",
                    revision="build-7", owner="data")
        displayed = {key: case[key] for key in ("service", "component", "revision")}
        with isolated_graph() as db:
            db.import_report(case)
            db.import_report({**case, "revision": "build-8"})
            with self.assertRaises(ValueError):
                db.record_review(case["id"], "data", False, expected_scope=displayed)
            self.assertEqual(db.context(case["id"])["review_history"], [])

    def test_local_actor_uses_effective_account_and_handles_unmapped_image_uid(self):
        case = dict(id="one", title="Owner routing", service="archive", component="retention",
                    revision="build-7", owner="data")
        with isolated_graph() as db:
            db.import_report(case)
            with patch.dict(os.environ, LOGNAME="untrusted-environment-label"):
                with patch("pwd.getpwuid", return_value=SimpleNamespace(pw_name="operator")) as lookup:
                    actor = db.record_review("one", "data", True)["actor"]
                    self.assertEqual(actor, "local:operator")
                    lookup.assert_called_once_with(os.geteuid())
                with patch("os.geteuid", return_value=10001), patch("pwd.getpwuid", side_effect=KeyError):
                    actor = db.record_review("one", "data", False)["actor"]
                    self.assertEqual(actor, "local:uid:10001")
                self.assertEqual(db.context("one")["review_history"][-1]["actor"], actor)
