"""Versioned remediation evidence stored in a dedicated HydraDB graph."""

import hashlib
import json
import os
import re
from pathlib import Path
from uuid import uuid4
from time import time_ns
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


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
        state = Path(os.getenv("XDG_STATE_HOME", Path.home() / ".local/state")) / "ichnix"
        state = Path(os.getenv("ICHNIX_STATE_DIR", state))
        token = os.getenv("ICHNIX_HYDRA_TOKEN")
        if not token:
            try:
                token = Path(os.getenv("ICHNIX_HYDRA_TOKEN_FILE", state / "hydra-token")).read_text().strip()
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
        self._put("Report", rid, key=key, snapshot=sid, **record)
        return key

    def cases(self):
        return self._query(f"MATCH (r:Report) RETURN {self._report_cols()} ORDER BY r.key")

    def _report(self, case_id):
        rows = self._query(f"MATCH (r:Report {{id:$id}}) RETURN {self._report_cols()}, r.snapshot AS snapshot",
                           id=_key("case", _case_id(case_id)))
        if not rows:
            raise KeyError(case_id)
        return rows[0]

    def context(self, case_id):
        report = self._report(case_id)
        snapshot = report.pop("snapshot")
        evidence = self._query("MATCH (s:Snapshot {id:$id})-[:HAS_EVIDENCE]->(e) "
                               "RETURN e.step AS step, e.result AS result, e.revision AS revision, e.run AS run_id ORDER BY e.observed",
                               id=snapshot)
        votes = self._query("MATCH (s:Snapshot {id:$id})-[:USES_COMPONENT]->(c)-[:HAS_REVIEW]->(v) "
                            "RETURN v.owner AS owner, v.accepted AS accepted", id=snapshot)
        counts = {report["owner"]: [1, 0]}
        for vote in votes:
            pair = counts.setdefault(vote["owner"], [0, 0])
            pair[0 if vote["accepted"] else 1] += 1
        items = [{"owner": owner, "score": round((yes + 1)/(yes + no + 2), 4),
                  "reason": f"catalog assignment and reviewed ownership for {report['component']} at {report['revision']}"}
                 for owner, (yes, no) in counts.items()]
        items.sort(key=lambda item: (-item["score"], item["owner"] != report["owner"], item["owner"]))
        return {"report": report, "recommendations": items, "evidence": evidence,
                "ranked_checks": self._ranked_checks(snapshot)}

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
        eid = _key("evidence", r["snapshot"], *parts)
        exists = self._query("MATCH (e:Evidence {id:$id}) RETURN e.id AS id", id=eid)
        if not exists:
            self._put("Evidence", eid, observed=time_ns(), run=run or "", **record)
        self._link(r["snapshot"], "HAS_EVIDENCE", eid)
        return {**record, "run_id": run}

    def record_review(self, case_id, owner, accepted):
        r = self._report(case_id)
        owner = _field({"owner": owner}, "owner")
        if not isinstance(accepted, bool):
            raise ValueError("accepted must be a boolean")
        vid = _key("review", r["snapshot"], owner)
        self._put("Review", vid, owner=owner, accepted=accepted)
        self._link(r["snapshot"], "REVIEWED", vid)
        self._link(_key("component", r["component"], r["revision"]), "HAS_REVIEW", vid)
        return {"owner": owner, "accepted": accepted}
