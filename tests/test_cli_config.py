import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from tempfile import TemporaryDirectory
import tomllib
import unittest

CLI = str(Path(__file__).resolve().parents[1] / "cli.py")


def cli(*args, **kwargs):
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True, timeout=10, **kwargs)


class OperatorTests(unittest.TestCase):
    def test_worker_profile_from_other_directory(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            secret = secrets.token_urlsafe(40)
            token = root / "worker"
            token.touch(mode=0o600)
            token.write_text(secret)
            args = ["mcp-config", "--profile", "worker", "--workspace-url", "http://127.0.0.1:8765",
                    "--worker-token-file", "worker", "--out", "client.json"]
            result = cli(*args, cwd=folder)
            self.assertEqual(result.returncode, 0, result.stderr)
            config = (root / "client.json").read_text()
            self.assertNotIn(secret, config + result.stdout + result.stderr)
            self.assertNotEqual(cli(*args, cwd=folder).returncode, 0)
            entry = json.loads(config)["mcpServers"]["ichnix-worker"]
            self.assertTrue(Path(entry["command"]).is_absolute())
            self.assertEqual(entry["env"]["ICHNIX_WORKER_TOKEN_FILE"], str(token))
            frames = [{"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                      {"method": "notifications/initialized"}, {"id": 2, "method": "tools/list"}]
            session = subprocess.run([entry["command"], *entry["args"]], env={**os.environ, **entry["env"]},
                                     input="".join(json.dumps(dict(jsonrpc="2.0", **f)) + "\n" for f in frames),
                                     cwd="/", text=True, capture_output=True, timeout=10, check=True)
            hello, tools = [json.loads(row)["result"] for row in session.stdout.splitlines()]
            self.assertEqual(hello["protocolVersion"], "2025-06-18")
            self.assertEqual({s["name"] for s in tools["tools"]}, {"claim_review", "get_review_context", "finish_review"})

    def test_invalid_or_missing_config_and_local_credentials(self):
        with TemporaryDirectory() as folder:
            token = Path(folder) / "token"
            args = ["mcp-config", "--profile", "local", "--hydra-token-file", str(token)]
            self.assertNotEqual(cli(*args).returncode, 0)
            token.touch(mode=0o600)
            token.write_text(secrets.token_urlsafe(48))
            result = cli(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            config = json.loads(result.stdout)["mcpServers"]["ichnix-local"]
            self.assertEqual(config["env"]["ICHNIX_HYDRA_TOKEN_FILE"], str(token))
            self.assertNotIn(token.read_text(), result.stdout)
            codex = cli(*args, "--format", "codex")
            self.assertEqual(tomllib.loads(codex.stdout)["mcp_servers"]["ichnix-local"], config)
            self.assertNotEqual(cli(*args, "--workspace-url", "https://inventory.example").returncode, 0)

    def test_identity_and_handoff_help_use_existing_entry_points(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)
            env = dict(os.environ, ICHNIX_IDENTITY_DB=str(path / "registry.sqlite3"))
            self.assertEqual(cli("identity", "issue", "sample", "--role", "worker",
                                 "--out", str(path / "worker"), env=env).returncode, 0)
            identities = json.loads(cli("identity", "list", env=env).stdout)
            self.assertEqual(identities[0]["role"], "worker")
            self.assertIn("enqueue", cli("handoff", "--help").stdout)
            self.assertIn("--policy", cli("staging", "--help").stdout)
