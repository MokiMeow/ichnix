import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from credentials import identify, issue
from handoffs import Handoffs


ROOT = Path(__file__).resolve().parents[1]


class StorageTests(unittest.TestCase):
    def test_init_check_permissions_lock_and_restart(self):
        with TemporaryDirectory() as directory:
            state = Path(directory) / "operator"
            registry, leases = state / "identities.sqlite3", state / "leases.sqlite3"
            env = dict(os.environ, ICHNIX_IDENTITY_DB=str(registry), ICHNIX_HANDOFF_DB=str(leases))
            def run(command):
                return subprocess.run([sys.executable, ROOT / "storage.py", command],
                                      env=env, capture_output=True, timeout=10)
            self.assertNotEqual(run("check").returncode, 0)
            self.assertFalse(state.exists())
            self.assertEqual(run("init").returncode, 0)
            with patch.dict(os.environ, env):
                issue(registry, "operator", "review", state / "credential")
                header = "Bearer " + (state / "credential").read_text().strip()
                self.assertEqual(identify(header), "operator")
                with sqlite3.connect(leases) as db:
                    db.execute("INSERT INTO jobs(id, case_id, service, component, revision, kind, created) VALUES "
                               "('lease','one','s','c','r','owner-review',1)")
                first = Handoffs().claim("one")
                self.assertEqual(run("init").returncode, 0)
                self.assertEqual(Handoffs().status(first["id"])["attempts"], 1)
                self.assertEqual(identify(header), "operator")
                self.assertEqual(run("check").returncode, 0)
                with sqlite3.connect(registry) as writer:
                    writer.execute("BEGIN EXCLUSIVE")
                    self.assertNotEqual(run("ready").returncode, 0)
                self.assertEqual(run("ready").returncode, 0)
                leases.unlink()
                with self.assertRaises(OSError):
                    Handoffs()
                self.assertFalse(leases.exists())
                registry.chmod(0o644)
                self.assertIsNone(identify(header))

    def test_readiness_and_full_integrity_have_different_costs(self):
        with TemporaryDirectory() as tmp:
            queue = Path(tmp) / "leases.sqlite"
            with patch.dict(os.environ, ICHNIX_HANDOFF_DB=str(queue)):
                from storage import checks
                Handoffs(queue)
                db = sqlite3.connect(queue)
                with db:
                    db.execute("CREATE TABLE archived(value TEXT)")
                    db.execute("INSERT INTO archived VALUES ('synthetic')")
                page = db.execute("SELECT rootpage FROM sqlite_schema WHERE name='archived'").fetchone()[0]
                size = db.execute("PRAGMA page_size").fetchone()[0]
                db.close()
                with queue.open("r+b") as file:
                    file.seek((page - 1) * size)
                    file.write(b"X")
                self.assertTrue(checks("ready")["handoff_db"])
                self.assertFalse(checks("check")["handoff_db"])

    def test_wrong_schema_permissions_and_symlinks(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            wrong = base / "wrong.sqlite"
            with sqlite3.connect(wrong) as db:
                db.execute("CREATE TABLE existing(value)")
            wrong.chmod(0o600)
            env = dict(os.environ, ICHNIX_HANDOFF_DB=str(wrong), ICHNIX_IDENTITY_DB=str(base / "registry.sqlite"))
            def init():
                return subprocess.run([sys.executable, ROOT / "storage.py", "init"], env=env, capture_output=True)
            self.assertNotEqual(init().returncode, 0)
            with sqlite3.connect(wrong) as db:
                self.assertEqual(db.execute("SELECT name FROM sqlite_schema").fetchall(), [("existing",)])
            link = base / "linked"
            link.symlink_to(wrong)
            env["ICHNIX_HANDOFF_DB"] = str(link)
            self.assertNotEqual(init().returncode, 0)
            directory = base / "shared"
            directory.mkdir(mode=0o777)
            directory.chmod(0o777)
            env["ICHNIX_HANDOFF_DB"] = str(directory / "jobs.sqlite")
            self.assertNotEqual(init().returncode, 0)
            self.assertFalse((directory / "jobs.sqlite").exists())
