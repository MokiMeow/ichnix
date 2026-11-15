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

    def test_signed_failures_find_differently_worded_reports(self):
        case = dict(id="base", title="Unexpected cache lifetime", service="billing",
                    component="archive", revision="r2", owner="records")
        with isolated_graph() as db:
            for key in ("base", "match", "one", "different", "unsigned", "pass", "split",
                        "other-service", "other-release", "other-component"):
                change = {"other-service": {"service": "other"}, "other-release": {"revision": "r1"},
                          "other-component": {"component": "other"}}.get(key, {})
                report = {**case, **change, "id": key}
                if key != "base":
                    report["title"] = "Blank PDF download"
                db.import_report(report)
                for index, step in enumerate(("expiry", "purge")):
                    single = key == "one" and index == 1
                    digest = "b" if key == "different" or (index and key == "split") else "a"
                    db.add_evidence(key, dict(step="expiry" if single else step,
                        result="passed" if key == "pass" else "failed", revision=report["revision"],
                        run_id=str(index), source_digest=digest * 64,
                        source_method="local-file" if key == "unsigned" else "hmac-sha256"))
            matches = db.context("base")["related_reports"]
            self.assertEqual([m["id"] for m in matches if m["possible_duplicate"]], ["match"])
            self.assertLess(matches[0]["score"], 0.2)
            self.assertIn("expiry", matches[0]["reason"])
            self.assertIn("purge", matches[0]["reason"])
            self.assertNotIn("related_reports", db.snapshot_context("base", include_operator=False)[1])
            db.import_report(dict(case, id="match", revision="r3"))
            self.assertFalse(any(m["possible_duplicate"] for m in db.context("base")["related_reports"]))

    def test_evidence_can_find_reports_beyond_the_title_shortlist(self):
        case = dict(id="source", title="Cache window", service="api", component="worker",
                    revision="r1", owner="triage")
        with isolated_graph() as db:
            db.import_report(case)
            for index in range(128):
                db.import_report(dict(case, id=f"early-{index:03}", title="Other symptom"))
            db.import_report(dict(case, id="zz-late", title="Broken invoice"))
            for key in ("source", "zz-late"):
                for step in ("ttl", "purge"):
                    db.add_evidence(key, dict(step=step, result="failed", revision="r1",
                        source_digest="c" * 64, source_method="hmac-sha256"))
            match, = db.context("source")["related_reports"]
            self.assertEqual(match["id"], "zz-late")
            self.assertTrue(match["possible_duplicate"])

    def test_generic_titles_alone_are_weak_evidence(self):
        case = dict(id="one", title="Security issue", service="api", component="worker",
                    revision="r1", owner="triage")
        with isolated_graph() as db:
            db.import_report(case)
            db.import_report(dict(case, id="two"))
            match, = db.context("one")["related_reports"]
            self.assertFalse(match["possible_duplicate"])

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
