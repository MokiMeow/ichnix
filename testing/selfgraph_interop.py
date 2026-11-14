"""Round-trip synthetic Ichnix evidence through the published SelfGraph SDK and HydraDB."""
import asyncio
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from selfgraph import HydraDBStore, SelfGraph

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ichnix.eval_graph import isolated_graph


async def consume(facts, case):
    uri = "bolt://127.0.0.1:" + os.environ["ICHNIX_BOLT_PORT"]
    token = Path(os.environ["ICHNIX_HYDRA_TOKEN_FILE"]).read_text().strip()
    namespace = "ichnix-interop"
    previous_relations = None
    for _ in range(2):
        # A fresh consumer on retry also checks persistence over a new Bolt session.
        async with SelfGraph(store=HydraDBStore(uri, auth=token), namespace=namespace) as graph:
            before = await graph.readiness()
            assert before.reachable and before.ready == (previous_relations is not None), before
            result = await graph.insert.facts(facts)
            entities = {entity.id: entity for entity in result.entities}
            observations = {entity.properties["run_id"]: entity.properties["result"]
                            for entity in entities.values() if entity.type == "ControlObservation"}
            assert observations == {"run-1": "failed", "run-2": "passed"}, observations
            for edge in result.relations:
                subject, target = entities[edge.from_entity], entities[edge.to_entity]
                assert (subject.type, subject.name) == ("ReportSnapshot", case["id"])
                assert (target.type, target.name) == {
                    "ON_REVISION": ("ComponentRevision", case["revision"]),
                    "HAS_OBSERVATION": ("ControlObservation", "retention_days"),
                }[edge.type]
                assert edge.source_count == 1 and edge.revision == 1, edge
            ready = await graph.readiness()
            assert ready.ready and ready.counts["entities"] == 4 and ready.counts["relations"] == 3, ready
            assert not ready.lint, ready.lint
            assert sorted(edge.type for edge in result.relations) == ["HAS_OBSERVATION", "HAS_OBSERVATION", "ON_REVISION"]
            relation_ids = {edge.id for edge in result.relations}
            if previous_relations is None:
                previous_relations = relation_ids
                continue
            assert relation_ids == previous_relations
            covered = await graph.check_answerable(
                "Which retention check belongs to this case and revision?",
                terms=[case["id"], case["revision"], "retention_days"],
            )
            absent = await graph.check_answerable(
                "Was an inventory audit observed?", terms=["inventory_audit"],
            )
            assert covered.verdict == "answerable" and covered.connected, covered
            assert absent.verdict == "not_answerable" and absent.missing == ["inventory_audit"], absent
    return {"selfgraph": version("selfgraph"), "counts": ready.counts, "retry": "same relations",
            "connected_case": covered.verdict, "absent_check": absent.verdict}


def main():
    assert version("selfgraph") == "0.2.0", "install testing/selfgraph-requirements.txt"
    case = json.loads((ROOT / "testing/fixtures/cases.json").read_text())[0]
    with isolated_graph() as graph, TemporaryDirectory() as tmp:
        graph.import_report(case)
        for run_id, result in (("run-1", "failed"), ("run-2", "passed")):
            graph.add_evidence(case["id"], {"step": "retention_days", "result": result,
                               "run_id": run_id, "revision": case["revision"]})
        out = Path(tmp) / "case-facts.json"
        subprocess.run([sys.executable, str(ROOT / "cli.py"), "export-selfgraph", case["id"],
                        "--out", str(out)], check=True, stdout=subprocess.DEVNULL, timeout=30)
        print(json.dumps(asyncio.run(consume(json.loads(out.read_text()), case)), indent=2))


if __name__ == "__main__":
    main()
