"""Initialize and inspect a workspace's local SQLite registry and review leases."""
import argparse
from contextlib import closing
import grp
import json
import os
from pathlib import Path
import pwd
import sqlite3
import stat
import sys


def private_primary_group(gid):
    try:
        user = pwd.getpwuid(os.geteuid())
        group = grp.getgrgid(gid)
    except KeyError:
        return False
    return (gid == user.pw_gid and group.gr_name == user.pw_name
            and set(group.gr_mem) <= {user.pw_name}
            and all(account.pw_uid == user.pw_uid for account in pwd.getpwall() if account.pw_gid == gid))


def connect_private(path, *, create=False, readonly=False, timeout=2):
    path = Path(path).absolute()
    parent = path.parent
    if create:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    for directory in (parent, *parent.parents):
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid()):
            raise ValueError("SQLite directory must be owned by the service account or root")
        if directory != parent and info.st_mode & stat.S_ISVTX:
            continue  # A trusted owner and sticky bit protect the existing child, e.g. in /tmp.
        if info.st_mode & 0o002 or (info.st_mode & 0o020 and
                (directory == parent or info.st_uid != os.geteuid() or not private_primary_group(info.st_gid))):
            raise ValueError("SQLite directory must prevent other users from replacing storage files")
    if create:
        try:
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
        except FileExistsError:
            pass
    for file in (path, *(Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal"))):
        try:
            info = file.lstat()
        except FileNotFoundError:
            if file == path:
                raise
            continue
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ValueError("SQLite files must be private regular files owned by the service account (0600)")
    mode = "ro" if readonly else "rw"
    db = sqlite3.connect(f"{path.as_uri()}?mode={mode}", uri=True, timeout=timeout)
    db.execute("PRAGMA trusted_schema=OFF")
    return db


def verify_tables(db, tables):
    for table, columns in tables.items():
        row = db.execute("SELECT type FROM sqlite_schema WHERE name=?", (table,)).fetchone()
        info = db.execute(f"PRAGMA table_info({table})").fetchall()
        if (row is None or row[0] != "table" or not set(columns).issubset(col[1] for col in info)
                or [col[1] for col in info if col[5]] != [columns[0]]):
            raise ValueError("incompatible SQLite schema")
    if "identities" in tables:
        indexes = db.execute("PRAGMA index_list(identities)").fetchall()
        if not any([row[2] for row in db.execute("SELECT * FROM pragma_index_info(?)", (index[1],))]
                   == ["digest"] for index in indexes if index[2] and not index[4]):
            raise ValueError("identity digests must be unique")


def configured_stores():
    from credentials import REGISTRY_TABLES, initialize
    from handoffs import Handoffs, LEASE_TABLES, handoff_path
    stores = []
    if "ICHNIX_IDENTITY_DB" in os.environ:
        stores.append(("identity_db", Path(os.environ["ICHNIX_IDENTITY_DB"]), REGISTRY_TABLES, initialize))
    leases = handoff_path()
    if stores and stores[0][1].absolute() == leases.absolute():
        raise ValueError("identity and lease paths must differ")
    stores.append(("handoff_db", leases, LEASE_TABLES, Handoffs))
    return stores


def checks(command):
    results = {}
    for name, path, tables, initialize in configured_stores():
        if (command == "ready" and name == "handoff_db"
                and "ICHNIX_HANDOFF_DB" not in os.environ and not path.is_symlink() and not path.exists()):
            continue  # Existing workspaces create their default queue upon first use.
        try:
            if command == "init":
                initialize(path)
            with closing(connect_private(path, readonly=True, timeout=0.5)) as db:
                verify_tables(db, tables)
                if name == "handoff_db" and db.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                    raise ValueError("lease database requires WAL")
                if command != "ready" and db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("SQLite integrity check failed")
            results[name] = True
        except (OSError, ValueError, sqlite3.Error):
            results[name] = False
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "check", "ready"))
    try:
        results = checks(parser.parse_args().command)
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"invalid storage configuration ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(json.dumps(results))
    return int(not all(results.values()))


if __name__ == "__main__":
    sys.exit(main())
