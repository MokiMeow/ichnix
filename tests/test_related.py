import unittest

from eval_graph import isolated_graph
from graph import _key


class RelatedReportsTests(unittest.TestCase):
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
