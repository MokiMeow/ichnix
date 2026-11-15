"""Durable local leases for reviewing existing graph context. All workers share one file."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

from ichnix.contracts import DISPOSITIONS
from ichnix.storage import connect_private, state_directory, verify_tables

LEASE_TABLES = {
    "jobs": ("id", "case_id", "service", "component", "revision", "kind", "state", "created",
             "worker", "expires", "token_hash", "attempts"),
    "job_reads": ("job_id", "attempt", "snapshot"),
    "outcomes": ("job_id", "snapshot", "disposition", "at"),
}

KINDS = tuple(DISPOSITIONS)
PUBLIC = "id, case_id, service, component, revision, kind, state, worker, expires, attempts"


def auto_handoffs_enabled():
    setting = os.getenv("ICHNIX_AUTO_HANDOFFS", "0")
    if setting not in ("0", "1"):
        raise ValueError("ICHNIX_AUTO_HANDOFFS must be 0 or 1")
    return setting == "1"


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _job_identity(context, kind):
    report = context["report"]
    basis = context["evidence" if kind == "evidence-review" else "recommendations"]
    if not basis:
        raise ValueError("nothing available to review")
    scope = [report[field] for field in ("id", "service", "component", "revision")]
    ident = _digest(json.dumps([scope, kind, basis], sort_keys=True, separators=(",", ":")))
    return ident, scope


def _default_db():
    graph = [os.getenv(name, default) for name, default in (
        ("ICHNIX_HYDRA_URL", "http://127.0.0.1:8443"), ("ICHNIX_HYDRA_NAMESPACE", "default"),
        ("ICHNIX_HYDRA_GRAPH", "default"), ("ICHNIX_HYDRA_CELL", "cell-0"))]
    return state_directory() / "handoffs" / (_digest(json.dumps(graph)) + ".sqlite3")


def handoff_path():
    if "ICHNIX_HANDOFF_DB" in os.environ:
        return Path(os.environ["ICHNIX_HANDOFF_DB"])
    return _default_db()


def _worker(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,120}", value):
        raise ValueError("invalid worker identifier")
    return value


class Handoffs:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else handoff_path()
        create = path is not None or "ICHNIX_HANDOFF_DB" not in os.environ
        with closing(self._connect(create=create)) as db:
            if not create:
                verify_tables(db, LEASE_TABLES)
                if db.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                    raise ValueError("lease database requires WAL")
                return
            existing = db.execute("SELECT name FROM sqlite_schema WHERE type='table'").fetchall()
            if existing:
                verify_tables(db, {"jobs": LEASE_TABLES["jobs"]})
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, case_id TEXT NOT NULL, service TEXT NOT NULL,
                component TEXT NOT NULL, revision TEXT NOT NULL, kind TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'queued', created INTEGER NOT NULL,
                worker TEXT, expires INTEGER, token_hash TEXT, attempts INTEGER NOT NULL DEFAULT 0)""")
            db.execute("""CREATE TABLE IF NOT EXISTS job_reads (
                job_id TEXT PRIMARY KEY, attempt INTEGER NOT NULL, snapshot INTEGER NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS outcomes (
                job_id TEXT PRIMARY KEY, snapshot INTEGER NOT NULL, disposition TEXT NOT NULL, at TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(state, expires, created)")
            verify_tables(db, LEASE_TABLES)
            db.commit()

    def _connect(self, *, create=False):
        db = connect_private(self.path, timeout=30, create=create)
        db.isolation_level = None
        db.row_factory = sqlite3.Row
        return db

    def enqueue(self, graph, case_id, kind, *, expected_scope=None):
        if kind not in KINDS:
            raise ValueError("invalid review kind")
        _, context = graph.snapshot_context(case_id, include_operator=False)
        report = context["report"]
        if expected_scope and any(report[k] != expected_scope[k] for k in ("service", "component", "revision")):
            raise ValueError("case mapping changed before review handoff")
        job_id, scope = _job_identity(context, kind)
        with closing(self._connect()) as db:
            db.execute("""INSERT INTO jobs(id, case_id, service, component, revision, kind, created)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""", (job_id, *scope, kind, time.time_ns()))
            return self._row(db, job_id)

    @staticmethod
    def _row(db, job_id):
        row = db.execute(f"SELECT {PUBLIC} FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError("unknown job")
        return dict(row)

    def status(self, job_id):
        with closing(self._connect()) as db:
            return self._row(db, job_id)

    def claim(self, worker, lease_seconds=120, *, graph_factory=None):
        _worker(worker)
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("lease must be 1..3600 seconds")
        with closing(self._connect()) as db:
            if graph_factory:
                pending = db.execute("SELECT id FROM jobs WHERE state='publishing' ORDER BY created LIMIT 1").fetchone()
                if pending:
                    self._publish(graph_factory(), pending[0])
            db.execute("BEGIN IMMEDIATE")
            now = int(time.time())
            pending = db.execute("""SELECT id FROM jobs WHERE state='queued'
                OR (state='leased' AND expires<=?) ORDER BY created, id LIMIT 1""", (now,)).fetchone()
            if pending is None:
                db.commit()
                return None
            token = secrets.token_urlsafe(32)
            db.execute("""UPDATE jobs SET state='leased', worker=?, expires=?, token_hash=?,
                          attempts=attempts+1 WHERE id=?""",
                       (worker, now + lease_seconds, _digest(token), pending["id"]))
            result = self._row(db, pending["id"])
            db.commit()
            return {**result, "token": token}

    @staticmethod
    def _owned_lease(db, job_id, worker, token):
        _worker(worker)
        if not isinstance(token, str) or not 0 < len(token) <= 256:
            raise ValueError("invalid claim token")
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row or worker != row["worker"] or not hmac.compare_digest(_digest(token), row["token_hash"] or ""):
            raise PermissionError("claim belongs to another worker or lease")
        return row

    def case_context(self, graph, job_id, worker, token):
        with closing(self._connect()) as db:
            def active():
                row = self._owned_lease(db, job_id, worker, token)
                if row["state"] != "leased" or row["expires"] <= int(time.time()):
                    raise PermissionError("lease inactive")
                return row
            job = active()
            snapshot, context = graph.snapshot_context(job["case_id"], include_operator=False)
            if any(context["report"][field] != job[field] for field in ("service", "component", "revision")):
                raise PermissionError("case mapping changed")
            # Graph retrieval can outlive a short lease or overlap completion.
            db.execute("BEGIN IMMEDIATE")
            job = active()
            if _job_identity(context, job["kind"])[0] == job_id:
                db.execute("INSERT OR REPLACE INTO job_reads VALUES (?, ?, ?)", (job_id, job["attempts"], snapshot))
            db.commit()
            return {key: context[key] for key in ("report", "evidence", "recommendations", "ranked_checks") if key in context}

    def _publish(self, graph, job_id):
        with closing(self._connect()) as db:
            event = db.execute("""SELECT o.snapshot, o.job_id, j.kind, j.worker, o.disposition, o.at
                FROM outcomes o JOIN jobs j ON j.id=o.job_id WHERE j.id=?""", (job_id,)).fetchone()
            graph.publish_outcome(*event)
            db.execute("UPDATE jobs SET state='complete' WHERE id=? AND state='publishing'", (job_id,))
            return {**self._row(db, job_id), "disposition": event["disposition"]}

    def finish(self, job_id, worker, token, *, disposition=None, graph=None):
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._owned_lease(db, job_id, worker, token)
            prior = db.execute("SELECT disposition FROM outcomes WHERE job_id=?", (job_id,)).fetchone()
            if prior:
                if prior[0] != disposition:
                    raise ValueError("completion disposition differs from recorded outcome")
                db.commit()
                if row["state"] == "complete":
                    return {**self._row(db, job_id), "disposition": disposition}
                return self._publish(graph, job_id)
            if row["state"] == "complete" and disposition is None:
                return self._row(db, job_id)
            if row["state"] != "leased" or row["expires"] <= int(time.time()):
                raise PermissionError("lease inactive")
            if disposition is None:
                db.execute("UPDATE jobs SET state='complete', expires=NULL WHERE id=?", (job_id,))
                result = self._row(db, job_id)
                db.commit()
                return result
            if disposition not in DISPOSITIONS[row["kind"]] or graph is None:
                raise ValueError("invalid disposition for this review")
            read = db.execute("SELECT snapshot FROM job_reads WHERE job_id=? AND attempt=?",
                              (job_id, row["attempts"])).fetchone()
            if not read:
                raise PermissionError("read the queued review material under this lease first")
            snapshot, context = graph.snapshot_context(row["case_id"], include_operator=False)
            if read[0] != snapshot or _job_identity(context, row["kind"])[0] != job_id:
                raise PermissionError("review material changed")
            if row["expires"] <= int(time.time()):
                raise PermissionError("lease expired during validation")
            at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            db.execute("INSERT INTO outcomes VALUES (?, ?, ?, ?)", (job_id, snapshot, disposition, at))
            db.execute("UPDATE jobs SET state='publishing', expires=NULL WHERE id=?", (job_id,))
            db.commit()
        return self._publish(graph, job_id)
