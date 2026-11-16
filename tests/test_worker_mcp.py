from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from ichnix.credentials import issue
from ichnix.eval_graph import isolated_graph
from ichnix.handoffs import Handoffs
from ichnix.server import ConsoleServer, Handler
from ichnix.worker_client import Worker, WorkspaceClient


class WorkerTests(unittest.TestCase):
    def test_local_stdio_exposes_only_read_tools(self):
        from ichnix.mcp_server import _dispatch
        request = {"id": 1, "jsonrpc": "2.0", "method": "tools/list"}
        _, response = _dispatch(request, "ready", None)
        self.assertEqual({row["name"] for row in response["result"]["tools"]}, {"list_cases", "get_case_context"})

    def test_redirects_and_unsafe_origins_fail_closed(self):
        calls = []

        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append(self.path)
                self.send_response(307)
                self.send_header("Location", "/unexpected")
                self.end_headers()

            def log_message(self, *args):
                pass

        with tempfile.TemporaryDirectory() as tmp, HTTPServer(("127.0.0.1", 0), Redirect) as server:
            token = Path(tmp) / "token"
            token.touch(mode=0o600)
            token.write_text(secrets.token_urlsafe(40))
            for url in ("http://example.com", "https://name:pass@localhost", "http://127.0.0.1/path",
                        "https://host?token=123", "file:///tmp/test"):
                with self.assertRaises(ValueError):
                    Worker(url, token)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = Worker(f"http://127.0.0.1:{server.server_port}", token)
                with self.assertRaises(RuntimeError):
                    client.call("claim_review", {})
                self.assertEqual(calls, ["/api/handoffs/claim"])
            finally:
                server.shutdown()
                thread.join()

    def test_hosted_mode_requires_tls_and_uses_only_org_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            token = Path(tmp) / "token"
            token.touch(mode=0o600)
            token.write_text(secrets.token_urlsafe(40))
            with self.assertRaises(ValueError):
                Worker("http://127.0.0.1", token, hosted=True)
            with patch("ichnix.worker_client.HTTPSConnection") as connect:
                client = WorkspaceClient("https://workspace.example", token, hosted=True)
                response = connect.return_value.getresponse.return_value
                response.headers.get_content_type.return_value = "application/json"
                response.status = 200
                response.read.return_value = b'{"name":"fixture-worker","role":"worker"}'
                self.assertEqual(client.auth_status()["role"], "worker")
                connect.return_value.request.assert_called_once_with("GET", "/org-api/auth/status", None,
                                                                     {"Authorization": "Bearer " + client.bearer})
                with self.assertRaises(ValueError):
                    client.request("GET", "//other.example")
                response.status = 307
                with self.assertRaises(RuntimeError):
                    client.request("POST", "/api/handoffs/claim", {})
                self.assertEqual(connect.return_value.request.call_count, 2)
                self.assertEqual(connect.return_value.request.call_args.args[:2], ("POST", "/org-api/handoffs/claim"))

    def test_remote_stdio_claim_context_and_finish(self):
        with isolated_graph() as graph, tempfile.TemporaryDirectory() as tmp:
            report = dict(id="mcp-item", title="Billing review", owner="finance", revision="r5",
                          service="billing", component="retention")
            graph.import_report(report)
            job = Handoffs().enqueue(graph, report["id"], "owner-review")
            token_file = Path(tmp) / "agent"
            registry = Path(tmp) / "registry.sqlite3"
            issue(registry, "worker-a", "worker", token_file)
            with patch.dict(os.environ, ICHNIX_IDENTITY_DB=str(registry)), ConsoleServer(("127.0.0.1", 0), Handler) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                env = dict(PATH=os.environ["PATH"], ICHNIX_WORKSPACE_URL=f"http://127.0.0.1:{server.server_port}",
                           ICHNIX_WORKER_TOKEN_FILE=str(token_file))
                events = [
                    ("initialize", {"protocolVersion": "2025-11-25"}),
                    ("notifications/initialized", {}),
                    ("tools/list", {}),
                    ("tools/call", {"name": "get_review_context", "arguments": {"job_id": job["id"]}}),
                    ("tools/call", {"name": "claim_review"}),
                    ("tools/call", {"name": "get_review_context", "arguments": {"job_id": job["id"], "format": "markdown"}}),
                    ("tools/call", {"name": "finish_review", "arguments": {"job_id": job["id"], "disposition": "needs-owner-review"}}),
                    ("tools/call", {"name": "get_review_context", "arguments": {"job_id": job["id"]}}),
                    ("tools/call", {"name": "finish_review", "arguments": {"job_id": "b" * 64}}),
                    ("tools/call", {"name": "finish_review", "arguments": {"job_id": job["id"]}}),
                    ("tools/call", {"name": "claim_review"}),
                ]
                frames = [{"jsonrpc": "2.0", "method": method, "params": params,
                           **({} if method.startswith("notifications/") else {"id": i})}
                          for i, (method, params) in enumerate(events)]
                try:
                    proc = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / "cli.py"),
                                           "mcp", "--worker"], input="".join(json.dumps(v) + "\n" for v in frames),
                                          text=True, capture_output=True, env=env, timeout=20)
                finally:
                    server.shutdown()
                    thread.join()
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn(token_file.read_text().strip(), proc.stdout + proc.stderr)
            result = {r["id"]: r["result"] for r in map(json.loads, proc.stdout.splitlines())}
            self.assertEqual({t["name"] for t in result[2]["tools"]}, {"claim_review", "get_review_context", "finish_review"})
            self.assertTrue(result[3]["isError"])
            claimed = json.loads(result[4]["content"][0]["text"])
            self.assertEqual(claimed["id"], job["id"])
            self.assertNotIn("token", claimed)
            self.assertEqual(result[5]["content"][0]["text"], graph.case_readme(report["id"]))
            self.assertTrue(result[6]["isError"])
            self.assertIn("get_review_context", result[6]["content"][0]["text"])
            self.assertEqual(json.loads(result[7]["content"][0]["text"])["report"], report)
            self.assertTrue(result[8]["isError"])
            self.assertEqual(json.loads(result[9]["content"][0]["text"])["state"], "complete")
            self.assertIsNone(json.loads(result[10]["content"][0]["text"]))
