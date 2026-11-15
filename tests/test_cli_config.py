import json
import os
from pathlib import Path
import secrets
import select
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch

from ichnix.credentials import issue, revoke
from ichnix.server import ConsoleServer, Handler
import tomllib
import unittest

CLI = str(Path(__file__).resolve().parents[1] / "cli.py")


def cli(*args, **kwargs):
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True, timeout=10, **kwargs)


class OperatorTests(unittest.TestCase):
    def test_worker_profile_from_other_directory(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            registry = root / "registry.sqlite"
            token = root / "worker"
            issue(registry, "fixture-worker", "worker", token)
            secret = token.read_text().strip()
            server = ConsoleServer(("127.0.0.1", 0), Handler)
            self.addCleanup(server.server_close)
            self.enterContext(patch.dict(os.environ, ICHNIX_IDENTITY_DB=str(registry), clear=True))
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join)
            self.addCleanup(server.shutdown)
            args = ["mcp-config", "--profile", "worker", "--workspace-url", f"http://127.0.0.1:{server.server_port}",
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
            def session():
                result = subprocess.run([entry["command"], *entry["args"]], env={**os.environ, **entry["env"]},
                                        input="".join(json.dumps(dict(jsonrpc="2.0", **f)) + "\n" for f in frames),
                                        cwd="/", text=True, capture_output=True, timeout=10, check=True)
                self.assertNotIn(secret, result.stdout + result.stderr)
                return [json.loads(row) for row in result.stdout.splitlines()]

            hello, tools = session()
            self.assertEqual(hello["result"]["protocolVersion"], "2025-06-18")
            self.assertEqual({s["name"] for s in tools["result"]["tools"]}, {"claim_review", "get_review_context", "finish_review"})
            with subprocess.Popen([entry["command"], *entry["args"]], env={**os.environ, **entry["env"]},
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as connected:
                try:
                    def send(frame):
                        connected.stdin.write(json.dumps(dict(jsonrpc="2.0", **frame)) + "\n")
                        connected.stdin.flush()
                        if "id" in frame:
                            self.assertTrue(select.select([connected.stdout], [], [], 5)[0], "MCP response timed out")
                            return json.loads(connected.stdout.readline())

                    self.assertIn("result", send(frames[0]))
                    send(frames[1])
                    revoke(registry, "fixture-worker")
                    denied = send({"id": 3, "method": "tools/call", "params": {"name": "claim_review"}})
                    self.assertTrue(denied["result"]["isError"])
                    self.assertEqual(send(frames[2])["error"]["code"], -32001)
                finally:
                    connected.stdin.close()
                    connected.wait(timeout=5)
            hello, tools = session()
            self.assertEqual(hello["error"]["code"], -32001)
            self.assertEqual(tools["error"]["code"], -32002)
            for role in ("read", "review", "intake"):
                issue(registry, role, role, root / role)
                entry["env"]["ICHNIX_WORKER_TOKEN_FILE"] = str(root / role)
                self.assertEqual(session()[0]["error"]["code"], -32001)

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
