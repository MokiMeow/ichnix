"""Versioned remediation evidence stored in a dedicated HydraDB graph."""

import hashlib
import pwd
from threading import Lock
from datetime import datetime, timezone
import json
import math
from collections import Counter
import os
import re
from pathlib import Path
from uuid import uuid4
from time import time, time_ns
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ichnix.handoffs import Handoffs, auto_handoffs_enabled
from ichnix.storage import state_directory
from ichnix import review_layout

_review_lock = Lock()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _key(kind, *parts):
    payload = json.dumps([kind, *parts], ensure_ascii=False).encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") >> 1


def _field(obj, name, limit=160):
    value = obj.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"invalid {name}")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"invalid {name}")
    return value.strip()


def _case_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9._:-]{1,160}", value):
        raise ValueError("invalid case id")
    return value


def _title_vector(title):
    """L2-normalized sparse character trigrams; coordinate space is fixed across processes."""
    counts = Counter()
    for word in re.findall(r"[^\W_]+", title.casefold()):
        padded = "^" + word + "$"
        for i in range(len(padded) - 2):
            gram = padded[i:i + 3].encode()
            index = hashlib.blake2s(gram, digest_size=2).hexdigest()
            counts[index] += 1
    norm = math.sqrt(sum(n * n for n in counts.values()))
    return {index: round(n / norm, 8) for index, n in sorted(counts.items())}


class Graph:
    """A single customer installation, with its own HydraDB namespace and cell."""

    def __init__(self):
        base = os.environ.get("ICHNIX_HYDRA_URL", "http://127.0.0.1:8443")
        parsed = urlsplit(base)
        if not parsed.hostname or parsed.username or parsed.password or not (
            parsed.scheme == "https" or parsed.scheme == "http" and parsed.hostname in
            ("127.0.0.1", "localhost", "::1")
        ):
            raise ValueError("HydraDB requires HTTPS or a loopback HTTP address")
        graph = os.getenv("ICHNIX_HYDRA_GRAPH", "default")
        self.url = f"{base.rstrip('/')}/v1/graphs/{quote(graph, safe='')}/query"
        self.cell = os.getenv("ICHNIX_HYDRA_CELL", "cell-0")
        token = os.getenv("ICHNIX_HYDRA_TOKEN")
        if not token:
            token_file = os.getenv("ICHNIX_HYDRA_TOKEN_FILE")
            if not token_file:
                token_file = state_directory() / "hydra-token"
            try:
                token = Path(token_file).read_text().strip()
            except OSError:
                raise RuntimeError("HydraDB credentials unavailable") from None
        self.headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json",
                        "X-Graph-Namespace": os.getenv("ICHNIX_HYDRA_NAMESPACE", "default")}
        self.http = build_opener(_NoRedirect())

    def _query(self, statement, **params):
        body = {"query_id": str(uuid4()), "cell_id": self.cell, "query": statement, "parameters": params,
                "page_size": 256, "consistency": "strong"}
        rows = []
        while True:
            req = Request(self.url, data=json.dumps(body).encode(), headers=self.headers)
            try:
                with self.http.open(req, timeout=15) as res:
                    data = json.load(res)
            except HTTPError as exc:
                raise RuntimeError(f"HydraDB query failed (HTTP {exc.code})") from None
            except (TimeoutError, URLError, ValueError):
                raise RuntimeError("HydraDB unavailable") from None
            if "rows" not in data or "error" in data:
                raise RuntimeError("HydraDB rejected query")
            rows.extend(dict(zip(data["columns"], (c.get("value") for c in row))) for row in data["rows"])
            if data.get("next_cursor") is None:
                return rows
            body.update(cursor=data["next_cursor"], query_id=data["query_id"])

    def _put(self, label, ident, **values):
        # Only internal identifiers enter the statement; values are parameters.
        fields = ", ".join(f"n.{k} = row.{k}" for k in values)
        self._query(f"UNWIND $rows AS row MERGE (n {{id: row.id}}) SET n:{label}, {fields}",
                    rows=[{"id": ident, **values}])

    def _link(self, src, relationship, dst):
        self._query(f"MERGE (a {{id:$src}})-[:{relationship}]->(b {{id:$dst}})", src=src, dst=dst)

    @staticmethod
    def _report_cols():
        return "r.key AS id, r.title AS title, r.service AS service, r.revision AS revision, r.component AS component, r.owner AS owner"

    def import_report(self, report):
        auto = auto_handoffs_enabled()
        record = {f: _field(report, f, 240 if f == "title" else 160)
                  for f in ("id", "title", "service", "revision", "component", "owner")}
        rid = _key("case", _case_id(report["id"]))
        # Revision-specific lineage means repeat imports and changed mappings do
        # not require deleting earlier evidence. Publish current pointer last.
        sid = _key("snapshot", record["id"], record["service"], record["component"], record["revision"])
        # A pre-service-key snapshot can be retained only for the report's
        # currently published mapping. Never infer legacy ownership by hash.
        prior = self._query("MATCH (r:Report {id:$id}) RETURN r.service AS service, "
                            "r.component AS component, r.revision AS revision, r.snapshot AS snapshot", id=rid)
        if prior and all(prior[0][key] == record[key] for key in ("service", "component", "revision")):
            sid = prior[0]["snapshot"]
        cid = _key("component", record["component"], record["revision"])
        self._put("ComponentRevision", cid, name=record["component"], revision=record["revision"])
        self._put("Snapshot", sid, revision=record["revision"])
        self._link(sid, "USES_COMPONENT", cid)
        self._link(rid, "HAS_SNAPSHOT", sid)
        key = record.pop("id")
        vector = json.dumps(_title_vector(record["title"]), separators=(",", ":"))
        self._put("Report", rid, key=key, snapshot=sid, title_vector_v1=vector, **record)
        self._prepare_diagram(sid, {"id": key, **record})
        if auto:
            Handoffs().enqueue(self, key, "owner-review", expected_scope=record)
        return key

    def _prepare_diagram(self, snapshot, report):
        facts = review_layout.classify(report)
        provider = review_layout.configured_provider()
        basis = json.dumps([1, facts, provider], sort_keys=True)
        rows = self._query("MATCH (s:Snapshot {id:$id}) RETURN s.layout_basis AS basis, s.layout_source AS source, "
                           "s.layout_attempts AS attempts, s.layout_retry AS retry", id=snapshot)
        prior = rows[0] if rows else {}
        attempts, now = 0, int(time())
        if prior.get("basis") == basis:
            attempts = prior.get("attempts") or 0
            if prior["source"] == provider or attempts >= 2 or (prior.get("retry") or 0) > now:
                return
        layout, source = review_layout.select_layout(facts, provider)
        self._put("Snapshot", snapshot, layout_basis=basis, review_layout=layout, layout_source=source,
                  layout_attempts=attempts + 1, layout_retry=now + 300)

    def review_diagram(self, case_id):
        snapshot, context = self.snapshot_context(case_id)
        rows = self._query("MATCH (s:Snapshot {id:$id}) RETURN s.review_layout AS layout, "
                           "s.layout_source AS source, s.layout_basis AS basis", id=snapshot)
        report = context["report"]
        facts = review_layout.classify(report)
        row = rows[0] if rows else {}
        source = row.get("source")
        valid_bases = [json.dumps([1, facts, mode], sort_keys=True) for mode in ("local", "workers-ai", "relay")]
        if row.get("basis") not in valid_bases or row.get("layout") not in review_layout.LAYOUTS or source not in (
                "local", "workers-ai", "relay"):
            layout, source = review_layout.local_layout(facts), "local"
        else:
            layout = row["layout"]
        evidence = context["evidence"]
        run = evidence[-1].get("run_id") if evidence else None
        results = Counter(e["result"] for e in evidence if e.get("run_id") == run)
        counts = {k: results[k] for k in ("passed", "failed", "inconclusive")}
        return {"layout": layout, "case": report["id"], "service": report["service"], "component": report["component"],
                "revision": report["revision"], "owner": context["recommendations"][0]["owner"], "controls": counts,
                "reviewed": bool(context["review_history"])}, source

    def cases(self):
        return self._query(f"MATCH (r:Report) RETURN {self._report_cols()} ORDER BY r.key")

    def case_page(self, direction=None, cursor=None):
        comparisons = {"after": ">", "before": "<", "from": ">="}
        params = {}
        where = ""
        if direction is not None:
            if direction not in comparisons:
                raise ValueError("invalid page direction")
            params["cursor"] = _case_id(cursor)
            where = f" WHERE r.key {comparisons[direction]} $cursor"
        backwards = direction == "before"
        order = "DESC" if backwards else "ASC"
        rows = self._query(
            f"MATCH (r:Report){where} RETURN r.key AS id, r.title AS title ORDER BY r.key {order} LIMIT 21",
            **params)
        more = len(rows) > 20
        cases = rows[:20]
        if backwards:
            cases.reverse()
        previous = next_cursor = None
        if cases:
            first, last = cases[0]["id"], cases[-1]["id"]
            other = False
            if direction:
                comparison, boundary = (">", last) if backwards else ("<", first)
                other = bool(self._query(
                    f"MATCH (r:Report) WHERE r.key {comparison} $boundary RETURN r.key AS id LIMIT 1",
                    boundary=boundary))
            has_previous, has_next = (more, other) if backwards else (other, more)
            previous = first if has_previous else None
            next_cursor = last if has_next else None
        return {"cases": cases, "previous": previous, "next": next_cursor}

    def _report(self, case_id):
        rows = self._query(f"MATCH (r:Report {{id:$id}}) RETURN {self._report_cols()}, r.snapshot AS snapshot, r.title_vector_v1 AS vector",
                           id=_key("case", _case_id(case_id)))
        if not rows:
            raise KeyError(case_id)
        return rows[0]

    def context(self, case_id):
        snapshot, context = self.snapshot_context(case_id)
        context["worker_outcomes"] = self._query(
            "MATCH (s:Snapshot {id:$id})-[:WORKER_OUTCOME]->(o) "
            "RETURN o.job AS job_id, o.kind AS kind, o.worker AS worker, o.disposition AS disposition, "
            "o.at AS at ORDER BY o.at", id=snapshot)
        return context

    def publish_outcome(self, snapshot, job_id, kind, worker, disposition, at):
        # The queue reserves these immutable fields before this replayable projection.
        eid = _key("worker-outcome", job_id)
        self._put("WorkerOutcome", eid, job=job_id, kind=kind, worker=worker, disposition=disposition, at=at)
        self._link(snapshot, "WORKER_OUTCOME", eid)

    def _snapshot_evidence(self, snapshot):
        return self._query("MATCH (s:Snapshot {id:$id})-[:HAS_EVIDENCE]->(e) "
                           "RETURN e.step AS step, e.result AS result, e.revision AS revision, e.run AS run_id, "
                           "e.digest AS source_digest, e.method AS source_method ORDER BY e.observed",
                           id=snapshot)

    def selfgraph_facts(self, case_id):
        report = self._report(case_id)
        snapshot = report["snapshot"]
        case = f"reportsnapshot:{snapshot:x}"
        component = report["component"]
        rev = report["revision"]
        component_id = f"componentrevision:{_key('component', component, rev):x}"
        entities = [
            {"id": case, "type": "ReportSnapshot", "name": report["id"], "properties": {"service": report["service"]}},
            {"id": component_id, "type": "ComponentRevision", "name": rev, "properties": {"component": component}},
        ]
        relations = [{"from_entity": case, "type": "ON_REVISION", "to_entity": component_id,
                      "evidence": [{"source_id": case}]}]
        for row in self._snapshot_evidence(snapshot):
            props = {key: row[key] for key in ("result", "run_id", "source_digest", "source_method") if row[key]}
            ident = _key("control-observation", snapshot, row["step"], props)
            observation = f"controlobservation:{ident:x}"
            entities.append({"id": observation, "type": "ControlObservation", "name": row["step"], "properties": props})
            source = "sha256:" + row["source_digest"] if row["source_digest"] else observation
            relations.append({"from_entity": case, "type": "HAS_OBSERVATION", "to_entity": observation,
                              "evidence": [{"source_id": source}]})
        return {"entities": entities, "relations": relations}

    def snapshot_context(self, case_id):
        report = self._report(case_id)
        snapshot = report.pop("snapshot")
        stored = report.pop("vector")
        vector = json.loads(stored) if stored else _title_vector(report["title"])
        evidence = self._snapshot_evidence(snapshot)
        legacy = self._query("MATCH (s:Snapshot {id:$id})-[:USES_COMPONENT]->(c)-[:HAS_REVIEW]->(v) "
                            "RETURN v.id AS decision, v.owner AS owner, v.accepted AS accepted", id=snapshot)
        audit = self._query("MATCH (s:Snapshot {id:$id})-[:AUDIT]->(v) "
                            "RETURN v.owner AS owner, v.accepted AS accepted, v.actor AS actor, "
                            "v.at AS at ORDER BY v.at", id=snapshot)
        events = self._query("MATCH (s:Snapshot {id:$id})-[:USES_COMPONENT]->(c)-[:HAS_REVIEW_EVENT]->(v) "
                             "RETURN v.subject AS decision, v.owner AS owner, v.accepted AS accepted, v.at AS at "
                             "ORDER BY v.at", id=snapshot)
        votes = {v["decision"]: v for v in (*legacy, *events)}
        counts = {report["owner"]: [1, 0]}
        for vote in votes.values():
            pair = counts.setdefault(vote["owner"], [0, 0])
            pair[0 if vote["accepted"] else 1] += 1
        items = [{"owner": owner, "score": round((yes + 1)/(yes + no + 2), 4),
                  "reason": f"catalog assignment and reviewed ownership for {report['component']} at {report['revision']}"}
                 for owner, (yes, no) in counts.items()]
        items.sort(key=lambda item: (-item["score"], item["owner"] != report["owner"], item["owner"]))
        return snapshot, {"report": report, "recommendations": items, "evidence": evidence,
                "ranked_checks": self._ranked_checks(snapshot), "review_history": audit,
                "related_reports": self._related_reports(snapshot, vector)}

    def _related_reports(self, snapshot, vector):
        if not vector:
            return []
        # Match only published, current snapshots in this installation's own graph.
        # Bound the candidate count before calculating lexical similarity.
        rows = self._query(
            "MATCH (s:Snapshot {id:$id})-[:USES_COMPONENT]->(c)"
            "<-[:USES_COMPONENT]-(o)<-[:HAS_SNAPSHOT]-(r:Report) "
            "WHERE o.id <> $id AND r.snapshot = o.id "
            "RETURN r.key AS id, r.title AS title, r.title_vector_v1 AS vector "
            "ORDER BY r.key LIMIT 128", id=snapshot)
        matches = []
        for row in rows:
            candidate = json.loads(row["vector"]) if row["vector"] else _title_vector(row["title"])
            score = sum(value * candidate.get(index, 0) for index, value in vector.items())
            if score >= 0.2:
                matches.append({"id": row["id"], "title": row["title"], "score": round(min(score, 1), 3),
                                "reason": "similar title on the same component revision"})
        return sorted(matches, key=lambda m: (-m["score"], m["id"]))[:5]

    def ranked_checks(self, case_id):
        return self._ranked_checks(self._report(case_id)["snapshot"])

    def _ranked_checks(self, snapshot):
        # Only observations from *other* case snapshots of this exact
        # component revision count. A retry of the same observation adds no vote.
        records = self._query("MATCH (s:Snapshot {id:$id})-[:USES_COMPONENT]->(c)"
                              "<-[:USES_COMPONENT]-(other)-[:HAS_EVIDENCE]->(e) "
                              "WHERE other.id <> $id RETURN other.id AS source, "
                              "e.step AS step, e.result AS result", id=snapshot)
        by_step = {}
        for row in records:
            sources = by_step.setdefault(row["step"], {})
            weight = {"failed": 2, "passed": 1}.get(row["result"], 0)
            sources[row["source"]] = max(sources.get(row["source"], 0), weight)
        items = [{"step": step, "score": sum(sources.values()), "observations": len(sources),
                  "reason": "prior CI evidence for this component revision"}
                 for step, sources in by_step.items() if any(sources.values())]
        return sorted(items, key=lambda item: (-item["score"], item["step"]))

    def add_evidence(self, case_id, evidence, *, expected_service=None, expected_component=None):
        r = self._report(case_id)
        if expected_service is not None and r["service"] != expected_service:
            raise ValueError("service mapping changed before evidence was recorded")
        if expected_component is not None and r["component"] != expected_component:
            raise ValueError("component mapping changed before evidence was recorded")
        record = {k: _field(evidence, k, 240) for k in ("step", "result", "revision")}
        if record["result"] not in ("passed", "failed", "inconclusive"):
            raise ValueError("invalid CI result")
        if r["revision"] != record["revision"]:
            raise ValueError("evidence revision differs from current case")
        run = _field(evidence, "run_id") if "run_id" in evidence else None
        parts = [*record.values(), run] if run else list(record.values())
        provenance = {}
        if "source_digest" in evidence or "source_method" in evidence:
            digest = _field(evidence, "source_digest", 64)
            method = _field(evidence, "source_method", 16)
            if not re.fullmatch(r"[0-9a-f]{64}", digest) or method not in {"hmac-sha256", "local-file"}:
                raise ValueError("invalid CI provenance")
            provenance = {"digest": digest, "method": method}
            parts.extend((digest, method))
        eid = _key("evidence", r["snapshot"], *parts)
        exists = self._query("MATCH (e:Evidence {id:$id}) RETURN e.id AS id", id=eid)
        if not exists:
            self._put("Evidence", eid, observed=time_ns(), run=run or "", **provenance, **record)
        self._link(r["snapshot"], "HAS_EVIDENCE", eid)
        return {**record, "run_id": run, "source_digest": provenance.get("digest"),
                "source_method": provenance.get("method")}

    def record_review(self, case_id, owner, accepted, *, actor=None, request_id=None, expected_scope=None):
        r = self._report(case_id)
        if expected_scope is not None and (not isinstance(expected_scope, dict) or any(
                expected_scope.get(k) != r[k] for k in ("service", "component", "revision"))):
            raise ValueError("case mapping changed since review began")
        owner = _field({"owner": owner}, "owner")
        if actor is None:
            uid = os.geteuid()
            try:
                actor = "local:" + pwd.getpwuid(uid).pw_name
            except KeyError:
                actor = f"local:uid:{uid}"
        actor = _field({"actor": actor}, "actor")
        if not isinstance(accepted, bool):
            raise ValueError("accepted must be a boolean")
        if request_id is None:
            request_id = str(uuid4())
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", request_id):
            raise ValueError("invalid idempotency key")
        subject = _key("review", r["snapshot"], owner)
        event = _key("review-event", case_id, actor, request_id)
        with _review_lock:
            prior = self._query("MATCH (v:ReviewEvent {id:$id}) RETURN v.accepted AS accepted, "
                                "v.subject AS subject, v.at AS at", id=event)
            if prior:
                if prior[0]["accepted"] != accepted or prior[0]["subject"] != subject:
                    raise ValueError("idempotency key already used for a different review")
                at = prior[0]["at"]
            else:
                at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
                self._put("ReviewEvent", event, subject=subject, owner=owner, accepted=accepted, actor=actor, at=at)
            # Retrying a partially linked event repairs its evidence trail.
            self._link(r["snapshot"], "AUDIT", event)
            self._link(_key("component", r["component"], r["revision"]), "HAS_REVIEW_EVENT", event)
        return {"owner": owner, "accepted": accepted, "actor": actor, "at": at}
