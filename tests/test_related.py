import unittest

from ichnix.eval_graph import isolated_graph
from ichnix.graph import _key


class RelatedReportsTests(unittest.TestCase):
    def test_duplicate_candidates_require_close_titles_and_the_same_deployment(self):
        case = dict(id="h1-10", title="Billing retention windows", service="billing",
                    component="archive", revision="r2", owner="records")
        with isolated_graph() as db:
            for change in [{}, {"id": "bc-20", "title": "Billing retention window"},
                           {"id": "other-service", "service": "catalog"},
                           {"id": "loose", "title": "Billing retention and masking policy review"},
                           {"id": "old", "revision": "r1"}]:
                db.import_report({**case, **change})
            matches = {r["id"]: r for r in db.context("h1-10")["related_reports"]}
            self.assertTrue(matches["bc-20"]["possible_duplicate"])
            self.assertFalse(matches["other-service"]["possible_duplicate"])
            self.assertFalse(matches["loose"]["possible_duplicate"])
            self.assertNotIn("old", matches)
            self.assertNotIn("h1-10", matches)
            db.import_report({**case, "id": "bc-20", "revision": "r3"})
            self.assertNotIn("bc-20", {r["id"] for r in db.context("h1-10")["related_reports"]})

    def test_vector_retrieval_excludes_stale_unrelated_and_own_reports(self):
        case = dict(id="base", title="Billing retention windows", service="api", component="ledger",
                    revision="v2", owner="billing")
        with isolated_graph() as db:
            db.import_report(case)
            for key, title in [("near", "Billing retention window"), ("far", "DNS fallback"),
                               ("identical", case["title"]), ("moved", case["title"])]:
                db.import_report(dict(case, id=key, title=title))
            db.import_report(dict(case, id="moved", revision="v3"))
            db.import_report(dict(case, id="other", component="unrelated"))
            matches = db.context("base")["related_reports"]
            self.assertEqual([m["id"] for m in matches], ["identical", "near"])
            self.assertEqual(matches[0]["score"], 1)
            self.assertLess(matches[1]["score"], matches[0]["score"])
            # Legacy records have no persisted vector. They must still match; reimport refreshes stale titles.
            for key in ["base", "identical"]:
                db._query("MATCH (r:Report {id:$id}) REMOVE r.title_vector_v1", id=_key("case", key))
            self.assertEqual(db.context("base")["related_reports"], matches)
            db.import_report(dict(case, id="near", title="DNS fallback"))
            self.assertEqual([m["id"] for m in db.context("base")["related_reports"]], ["identical"])
