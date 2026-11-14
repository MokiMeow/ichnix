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


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _connect(path, readonly=False):
    path = Path(path).absolute()
    if readonly:
        return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.exists():
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        except FileExistsError:
            pass
    if path.stat().st_mode & 0o077:
        raise ValueError("identity database must have mode 0600")
    db = sqlite3.connect(path, timeout=2)
    db.execute("""CREATE TABLE IF NOT EXISTS identities
        (name TEXT PRIMARY KEY, role TEXT NOT NULL, digest TEXT NOT NULL UNIQUE,
         issued TEXT NOT NULL, revoked TEXT)""")
    return db


def _digest(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def issue(path, name, role, output):
    if not re.fullmatch(r"[a-zA-Z0-9_.@-]{1,80}", name) or role not in ("read", "review", "worker"):
        raise ValueError("invalid reviewer name or role")
    if role == "worker" and not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", name):
        raise ValueError("worker names allow letters, numbers, periods, hyphens and underscores")
    token = secrets.token_urlsafe(40)
    with closing(_connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        prior = db.execute("SELECT revoked FROM identities WHERE name=?", (name,)).fetchone()
        if prior is not None and prior[0] is None:
            raise ValueError("identity already active; revoke it before issuing another token")
        db.execute("INSERT OR REPLACE INTO identities VALUES(?,?,?,?,NULL)", (name, role, _digest(token), utcnow()))
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
        count = db.execute("UPDATE identities SET revoked=? WHERE name=? AND revoked IS NULL", (utcnow(), name)).rowcount
        if count != 1:
            raise ValueError("no active identity matches")


def identities(path):
    with closing(_connect(path, readonly=True)) as db:
        return [{"name": n, "role": r, "issued": t, "revoked": end} for n, r, t, end in
                db.execute("SELECT name,role,issued,revoked FROM identities ORDER BY name")]


def identify(header, edit=False, worker=False):
    prefix = "Bearer "
    if not header.startswith(prefix):
        return None
    token = header[len(prefix):]
    if not token.isascii() or not 32 <= len(token) <= 512:
        return None
    if "ICHNIX_IDENTITY_DB" in os.environ:
        try:
            with closing(_connect(os.environ["ICHNIX_IDENTITY_DB"], readonly=True)) as db:
                row = db.execute("SELECT name,role FROM identities WHERE digest=? AND revoked IS NULL",
                                 (_digest(token),)).fetchone()
        except (sqlite3.Error, OSError, ValueError):
            return None
        roles = ("worker",) if worker else (("review",) if edit else ("review", "read", "worker"))
        if row and row[1] in roles:
            return row[0]
        return None
    if worker:
        return None
    roles = [("ICHNIX_REVIEW_TOKEN", "shared:review")]
    if not edit:
        roles.append(("ICHNIX_READ_TOKEN", "shared:read"))
    return next((name for key, name in roles if hmac.compare_digest(token.encode(), os.getenv(key, "").encode())), None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    cmds = parser.add_subparsers(dest="command", required=True)
    add = cmds.add_parser("issue")
    add.add_argument("name")
    add.add_argument("--role", choices=["read", "review", "worker"], required=True)
    add.add_argument("--out", type=Path, required=True, help="new private token file outside the application directory")
    cmds.add_parser("revoke").add_argument("name")
    cmds.add_parser("list")
    args = parser.parse_args()
    path = os.getenv("ICHNIX_IDENTITY_DB")
    if not path:
        parser.error("set ICHNIX_IDENTITY_DB to a private database path")
    try:
        if args.command == "issue":
            if args.out.resolve().is_relative_to(Path(__file__).resolve().parent):
                parser.error("place the private token file outside the application directory")
            issue(path, args.name, args.role, args.out)
            print("token written to the requested private file")
        elif args.command == "revoke":
            revoke(path, args.name)
            print("identity revoked")
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
