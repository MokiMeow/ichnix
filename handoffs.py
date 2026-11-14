"""Durable local leases for reviewing existing graph context. All workers share one file."""
from contextlib import closing
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

KINDS = ("owner-review", "evidence-review")
PUBLIC = "id, case_id, service, component, revision, kind, state, worker, expires, attempts"


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _default_db():
    base = Path(os.getenv("XDG_STATE_HOME", Path.home() / ".local/state")) / "ichnix"
    base = Path(os.getenv("ICHNIX_STATE_DIR", base))
    graph = [os.getenv(name, default) for name, default in (
        ("ICHNIX_HYDRA_URL", "http://127.0.0.1:8443"), ("ICHNIX_HYDRA_NAMESPACE", "default"),
        ("ICHNIX_HYDRA_GRAPH", "default"), ("ICHNIX_HYDRA_CELL", "cell-0"))]
    return base / "handoffs" / (_digest(json.dumps(graph)) + ".sqlite3")


def _worker(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,120}", value):
        raise ValueError("invalid worker identifier")
    return value


class Handoffs:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else _default_db()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, case_id TEXT NOT NULL, service TEXT NOT NULL,
                component TEXT NOT NULL, revision TEXT NOT NULL, kind TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'queued', created INTEGER NOT NULL,
                worker TEXT, expires INTEGER, token_hash TEXT, attempts INTEGER NOT NULL DEFAULT 0)""")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(state, expires, created)")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        return db

    def enqueue(self, graph, case_id, kind):
        if kind not in KINDS:
            raise ValueError("invalid review kind")
        context = graph.context(case_id)
        report = context["report"]
        basis = context["evidence" if kind == "evidence-review" else "recommendations"]
        if not basis:
            raise ValueError("nothing available to review")
        scope = [report[field] for field in ("id", "service", "component", "revision")]
        job_id = _digest(json.dumps([scope, kind, basis], sort_keys=True, separators=(",", ":")))
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

    def claim(self, worker, lease_seconds=120):
        _worker(worker)
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("lease must be 1..3600 seconds")
        with closing(self._connect()) as db:
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
            context = graph.context(job["case_id"])
            if any(context["report"][field] != job[field] for field in ("service", "component", "revision")):
                raise PermissionError("case mapping changed")
            # Graph retrieval can outlive a short lease or overlap completion.
            active()
            return {key: context[key] for key in ("report", "evidence", "recommendations", "ranked_checks") if key in context}

    def finish(self, job_id, worker, token):
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._owned_lease(db, job_id, worker, token)
            if row["state"] != "complete":
                if row["state"] != "leased" or row["expires"] <= int(time.time()):
                    raise PermissionError("lease expired")
                db.execute("UPDATE jobs SET state='complete', expires=NULL WHERE id=?", (job_id,))
            result = self._row(db, job_id)
            db.commit()
            return result
