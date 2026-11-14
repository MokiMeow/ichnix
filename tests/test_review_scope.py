from unittest import TestCase

from ichnix.eval_graph import isolated_graph


class ReviewScopeTests(TestCase):
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
