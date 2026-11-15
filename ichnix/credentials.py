"""Manage named bearer credentials for a self-hosted Ichnix workspace."""

import argparse
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
import sys

from ichnix.storage import connect_private, verify_tables

REGISTRY_TABLES = {"identities": ("name", "role", "digest", "issued", "revoked")}
EVENT_TABLE = {"identity_events": ("id", "name", "role", "event", "at", "actor")}
LEGACY_EVENTS = """SELECT name, role, 'issued' AS event, issued AS at, NULL AS actor FROM identities
                   UNION ALL
                   SELECT name, role, 'revoked', revoked, NULL FROM identities WHERE revoked IS NOT NULL"""


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _connect(path, readonly=False, create=False):
    db = connect_private(path, create=create, readonly=readonly)
    try:
        if not readonly:
            db.execute("BEGIN IMMEDIATE")
        if create:
            existing = db.execute("SELECT name FROM sqlite_schema WHERE type='table'").fetchall()
            if not existing:
                db.execute("""CREATE TABLE identities
                    (name TEXT PRIMARY KEY, role TEXT NOT NULL, digest TEXT NOT NULL UNIQUE,
                     issued TEXT NOT NULL, revoked TEXT)""")
        verify_tables(db, REGISTRY_TABLES)
        if not readonly:
            if not _has_events(db):
                db.execute("""CREATE TABLE identity_events (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,
                    event TEXT NOT NULL CHECK(event IN ('issued', 'revoked', 'reissued')),
                    at TEXT NOT NULL, actor TEXT)""")
                db.execute(f"""INSERT INTO identity_events(name,role,event,at,actor)
                               SELECT * FROM ({LEGACY_EVENTS}) ORDER BY at,name,event""")
            verify_tables(db, EVENT_TABLE)
    except BaseException:
        db.close()
        raise
    return db


def _has_events(db):
    return db.execute("SELECT 1 FROM sqlite_schema WHERE name='identity_events'").fetchone() is not None


def initialize(path):
    with closing(_connect(path, create=True)) as db:
        db.commit()


def _record(db, name, role, event, at):
    db.execute("INSERT INTO identity_events(name,role,event,at,actor) VALUES(?,?,?,?,?)",
               (name, role, event, at, f"uid:{os.geteuid()}"))


def _digest(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def issue(path, name, role, output):
    if not re.fullmatch(r"[a-zA-Z0-9_.@-]{1,80}", name) or role not in ("read", "review", "worker", "intake"):
        raise ValueError("invalid reviewer name or role")
    if role == "worker" and not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", name):
        raise ValueError("worker names allow letters, numbers, periods, hyphens and underscores")
    token = secrets.token_urlsafe(40)
    with closing(_connect(path, create=True)) as db, db:
        prior = db.execute("SELECT revoked FROM identities WHERE name=?", (name,)).fetchone()
        if prior is not None and prior[0] is None:
            raise ValueError("identity already active; revoke it before issuing another token")
        at = utcnow()
        db.execute("""INSERT INTO identities(name,role,digest,issued,revoked) VALUES(?,?,?,?,NULL)
                      ON CONFLICT(name) DO UPDATE SET role=excluded.role, digest=excluded.digest,
                      issued=excluded.issued, revoked=NULL""", (name, role, _digest(token), at))
        _record(db, name, role, "reissued" if prior is not None else "issued", at)
        fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(token + "\n")
            db.commit()
        except BaseException:
            Path(output).unlink(missing_ok=True)
            raise


def revoke(path, name):
    with closing(_connect(path)) as db, db:
        at = utcnow()
        row = db.execute("UPDATE identities SET revoked=? WHERE name=? AND revoked IS NULL RETURNING role",
                         (at, name)).fetchone()
        if row is None:
            raise ValueError("no active identity matches")
        _record(db, name, row[0], "revoked", at)


def identities(path):
    with closing(_connect(path, readonly=True)) as db:
        return [{"name": n, "role": r, "issued": t, "revoked": end} for n, r, t, end in
                db.execute("SELECT name,role,issued,revoked FROM identities ORDER BY name")]


def history(path, name=None):
    with closing(_connect(path, readonly=True)) as db:
        db.execute("BEGIN")
        fields = "name,role,event,at,actor"
        if _has_events(db):
            verify_tables(db, EVENT_TABLE)
            source, order = "identity_events", "id"
        else:
            source, order = f"({LEGACY_EVENTS})", "at,name,event"
        where, params = (" WHERE name=?", (name,)) if name is not None else ("", ())
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(f"SELECT {fields} FROM {source}{where} ORDER BY {order}", params)]


def principal(header):
    if not header.startswith("Bearer "):
        return None
    token = header[len("Bearer "):]
    if not token.isascii() or not 32 <= len(token) <= 512:
        return None
    if "ICHNIX_IDENTITY_DB" in os.environ:
        try:
            with closing(_connect(os.environ["ICHNIX_IDENTITY_DB"], readonly=True)) as db:
                row = db.execute("SELECT name,role FROM identities WHERE digest=? AND revoked IS NULL",
                                 (_digest(token),)).fetchone()
        except (sqlite3.Error, OSError, ValueError):
            return None
        if row and row[1] in ("read", "review", "intake", "worker"):
            return {"name": row[0], "role": row[1]}
        return None
    for role, key in (("review", "ICHNIX_REVIEW_TOKEN"), ("read", "ICHNIX_READ_TOKEN")):
        if hmac.compare_digest(token.encode(), os.getenv(key, "").encode()):
            return {"name": "shared:" + role, "role": role}
    return None


def identify(header, edit=False, worker=False, intake=False):
    identity = principal(header)
    roles = ("review", "read", "intake")
    if worker or intake or edit:
        roles = ("worker" if worker else "intake" if intake else "review",)
    return identity["name"] if identity and identity["role"] in roles else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    cmds = parser.add_subparsers(dest="command", required=True)
    add = cmds.add_parser("issue")
    add.add_argument("name")
    add.add_argument("--role", choices=["read", "review", "worker", "intake"], required=True)
    add.add_argument("--out", type=Path, required=True, help="new private token file outside the application directory")
    cmds.add_parser("revoke").add_argument("name")
    cmds.add_parser("list")
    cmds.add_parser("history", help="show credential lifecycle events on this host").add_argument("name", nargs="?")
    args = parser.parse_args()
    path = os.getenv("ICHNIX_IDENTITY_DB")
    if not path:
        parser.error("set ICHNIX_IDENTITY_DB to a private database path")
    try:
        if args.command == "issue":
            if args.out.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
                parser.error("place the private token file outside the application directory")
            issue(path, args.name, args.role, args.out)
            print("token written to the requested private file")
        elif args.command == "revoke":
            revoke(path, args.name)
            print("identity revoked")
        elif args.command == "history":
            print(json.dumps(history(path, args.name), indent=2))
        else:
            print(json.dumps(identities(path), indent=2))
    except ValueError as exc:
        parser.error(str(exc))
    except (OSError, sqlite3.Error) as exc:
        print(f"identity operation failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
