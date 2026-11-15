"""Replay the README's synthetic owner-routing comparison on disposable HydraDB."""
import json
import random

from ichnix.eval_graph import isolated_graph

COMPONENT_OWNERS = (
    ("sessions", "identity"), ("accounts", "identity"), ("archive", "records"),
    ("export", "records"), ("billing", "finance"), ("invoices", "finance"),
    ("telemetry", "privacy"), ("consent", "privacy"), ("gateway", "platform"),
    ("catalog", "platform"), ("backup", "storage"), ("index", "search"),
    ("profile", "identity"), ("events", "records"), ("metrics", "privacy"),
    ("registry", "platform"),
)


def evaluate(graph):
    training, heldout = [], []
    for index, (component, owner) in enumerate(COMPONENT_OWNERS):
        for slot, service in enumerate(("api", "worker", "portal", "sync", "batch", "edge")):
            train = slot < 2
            revision = "r1" if train or index < 12 else "r2"
            expected = "assurance" if slot == 5 and index % 2 == 0 else owner
            report = dict(id=f"R-{index:02}-{slot}", title=f"{component} {service} review",
                          component=component, revision=revision, service=service,
                          owner=owner if slot == 2 else "triage")
            pooled = dict(report, id="pool-" + report["id"], component="all", revision="all")
            if train:
                training.append((report, pooled, expected))
            else:
                heldout.append((graph.import_report(report), graph.import_report(pooled), expected,
                                report["owner"], revision == "r2"))
    random.Random(71).shuffle(training)
    curve = []
    for count in range(0, len(training) + 1, 8):
        if count:
            for scoped, pooled, expected in training[count-8:count]:
                for report in (scoped, pooled):
                    key = graph.import_report(report)
                    graph.record_review(key, expected, True, actor="fixture-reviewer")
        scores = dict(training=count, heldout=len(heldout), scoped=0, pooled=0, catalog=0, unseen_scoped=0)
        for scoped, pooled, expected, catalog, unseen in heldout:
            scores["catalog"] += catalog == expected
            for model, key in (("scoped", scoped), ("pooled", pooled)):
                context = graph.snapshot_context(key, include_operator=False)[1]
                correct = context["recommendations"][0]["owner"] == expected
                scores[model] += correct
                if unseen and model == "scoped":
                    scores["unseen_scoped"] += correct
        curve.append(scores)
    return curve


if __name__ == "__main__":
    with isolated_graph() as graph:
        print(json.dumps(evaluate(graph), indent=2))
