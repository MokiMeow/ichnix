"""Native remote client contract. Runs without HydraDB or Unix account modules."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import tomllib
import unittest

CLI = str(Path(__file__).resolve().parents[1] / "cli.py")


class DesktopClientTests(unittest.TestCase):
    def test_remote_cli_and_worker_profile(self):
        worker, human, intake = (secrets.token_urlsafe(40) for _ in range(3))
        received = []
        report = dict(id="desk-1", title="Data retention", component="archive",
                      revision="r7", service="ledger", owner="records")

        class Workspace(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, data, status=200):
                raw = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                role = {"Bearer " + worker: "worker", "Bearer " + human: "review"}.get(self.headers.get("Authorization"))
                if self.path == "/api/auth/status" and role:
                    return self.respond({"name": "desk-" + role, "role": role})
                if role == "review" and self.path == "/api/cases/desk-1":
                    return self.respond({"report": report})
                self.respond({}, 403)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                role = self.headers.get("Authorization")
                if self.path == "/api/handoffs/claim" and role == "Bearer " + worker:
                    received.append(body)
                    return self.respond(None)
                if self.path == "/api/cases/desk-1/reviews" and role == "Bearer " + human:
                    return self.respond({"accepted": body["accepted"], "scope": body["scope"]})
                if self.path == "/api/import/report" and role == "Bearer " + intake and body == report:
                    return self.respond({"id": body["id"]})
                self.respond({}, 403)

        with TemporaryDirectory() as tmp, HTTPServer(("127.0.0.1", 0), Workspace) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            env = {k: v for k, v in os.environ.items() if not k.startswith("ICHNIX_")}
            env.update(ICHNIX_WORKSPACE_URL=f"http://127.0.0.1:{server.server_port}",
                       ICHNIX_WORKER_TOKEN=worker, ICHNIX_USER_TOKEN=human)

            def cli(*args, **opts):
                run = subprocess.run([sys.executable, CLI, *args], cwd=tmp, env=env, capture_output=True,
                                     text=True, timeout=10, **opts)
                self.assertTrue(all(secret not in run.stdout + run.stderr for secret in (human, worker, intake)))
                self.assertEqual(run.returncode, 0, run.stderr)
                return run.stdout

            try:
                auth = json.loads(cli("auth", "status", "--token-env", "ICHNIX_USER_TOKEN"))
                self.assertEqual(auth["name"], "desk-review")
                result = json.loads(cli("human", "--token-env", "ICHNIX_USER_TOKEN", "review", "desk-1", "records", "--accept"))
                self.assertTrue(result["accepted"])
                self.assertEqual(result["scope"]["revision"], report["revision"])
                path = Path(tmp) / "mapped.json"
                path.write_text(json.dumps(report), encoding="utf-8-sig")
                env["ICHNIX_USER_TOKEN"] = intake
                self.assertEqual(json.loads(cli("human", "--token-env", "ICHNIX_USER_TOKEN", "import-report", str(path))),
                                 {"id": report["id"]})
                args = ("mcp-config", "--profile", "worker", "--token-env", "ICHNIX_WORKER_TOKEN")
                del env["ICHNIX_WORKER_TOKEN"]
                for name in ("PATH", "ICHNIX_USER_TOKEN", worker):
                    bad = subprocess.run([sys.executable, CLI, "mcp-config", "--profile", "worker", "--token-env", name],
                                         cwd=tmp, env=env, capture_output=True, text=True, timeout=10)
                    self.assertNotEqual(bad.returncode, 0)
                    self.assertTrue(worker not in bad.stdout + bad.stderr)
                config = json.loads(cli(*args))["mcpServers"]["ichnix-worker"]
                codex = tomllib.loads(cli(*args, "--format", "codex"))["mcp_servers"]["ichnix-worker"]
                self.assertEqual(codex["env_vars"], ["ICHNIX_WORKER_TOKEN"])
                self.assertTrue(Path(config["command"]).is_absolute())
                env["ICHNIX_WORKER_TOKEN"] = worker
                frames = [
                    {"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
                    {"method": "notifications/initialized"}, {"id": 2, "method": "tools/list"},
                    {"id": 3, "method": "tools/call", "params": {"name": "claim_review"}},
                ]
                run = subprocess.run([config["command"], *config["args"]], cwd=tmp, env={**env, **config["env"]},
                                     input="".join(json.dumps(dict(jsonrpc="2.0", **f)) + "\n" for f in frames),
                                     text=True, capture_output=True, timeout=10)
                self.assertTrue(all(secret not in run.stdout + run.stderr for secret in (human, worker, intake)))
                self.assertEqual(run.returncode, 0, run.stderr)
                hello, tools, claim = [json.loads(line)["result"] for line in run.stdout.splitlines()]
                self.assertEqual(hello["protocolVersion"], "2025-11-25")
                self.assertEqual({tool["name"] for tool in tools["tools"]}, {"claim_review", "get_review_context", "finish_review"})
                self.assertIsNone(json.loads(claim["content"][0]["text"]))
                self.assertEqual(received, [{"lease_seconds": 120}])
                file = Path(tmp) / "untrusted-token"
                file.write_text(worker)
                env["ICHNIX_WORKER_TOKEN_FILE"] = str(file)
                del env["ICHNIX_WORKER_TOKEN"]
                denied = subprocess.run([sys.executable, CLI, "auth", "status", "--token-env", "ICHNIX_WORKER_TOKEN"],
                                        cwd=tmp, env=env, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(denied.returncode, 0)
                file.chmod(0o644)
                denied = subprocess.run([sys.executable, CLI, "auth", "status", "--token-file", str(file)],
                                        cwd=tmp, env=env, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(denied.returncode, 0)
                self.assertTrue(worker not in denied.stdout + denied.stderr)
            finally:
                server.shutdown()
                thread.join()


if __name__ == "__main__":
    unittest.main()
