import hashlib
import hmac
import importlib.util
import json
import os
from contextlib import ExitStack
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from ichnix.credentials import issue
from ichnix.contracts import ReviewContextRequired
from ichnix.eval_graph import isolated_graph
from ichnix.handoffs import Handoffs
from ichnix.server import ConsoleServer, Handler
from ichnix import report_context

ROOT = Path(__file__).resolve().parents[1]
CASE = dict(
    id="h1-example-42",
    title="Archive | retention",
    service="ledger",
    component="archive",
    revision="r4",
    owner="records",
)


class ReportContextTests(unittest.TestCase):
    def test_archive_before_publication_and_private_exports(self):
        with isolated_graph() as graph, TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            registry = root / "identities.sqlite"
            issue(registry, "writer", "intake", root / "token")
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps({"program": "example", "reports": {"42": CASE}}))
            stack.enter_context(
                patch.dict(
                    os.environ,
                    ICHNIX_ARCHIVE_BUCKET="qa-example",
                    ICHNIX_IDENTITY_DB=str(registry),
                    ICHNIX_WEBHOOK_SECRET="qa-test",
                    ICHNIX_CATALOG=str(catalog),
                    ICHNIX_PROGRAM="example",
                )
            )
            objects = {}
            attempted = []

            def store(data):
                attempted.append(data)
                if len(attempted) == 1:
                    raise RuntimeError("unavailable")
                objects[hashlib.sha256(data).hexdigest()] = data

            stack.enter_context(patch.object(report_context, "archive_readme", side_effect=store))
            server = stack.enter_context(ConsoleServer(("127.0.0.1", 0), Handler))
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            stack.callback(thread.join)
            stack.callback(server.shutdown)

            def post(route, data, headers):
                conn = HTTPConnection(*server.server_address, timeout=10)
                try:
                    conn.request("POST", route, data, {"Content-Type": "application/json", **headers})
                    response = conn.getresponse()
                    response.read()
                    return response.status
                finally:
                    conn.close()

            data = json.dumps(CASE).encode()
            auth = {"Authorization": "Bearer " + (root / "token").read_text().strip()}
            self.assertEqual(post("/api/import/report", data, {}), 403)
            self.assertEqual(attempted, [])
            self.assertEqual(post("/api/import/report", data, auth), 503)
            self.assertEqual(graph.cases(), [])
            self.assertEqual(post("/api/import/report", data, auth), 200)
            report = {
                "type": "report",
                "id": "42",
                "attributes": {
                    "title": CASE["title"],
                    "state": "triaged",
                    "vulnerability_information": "private provider narrative",
                },
            }
            delivery = json.dumps({"data": {"report": report}}).encode()
            headers = {"X-H1-Signature": "sha256=" + hmac.digest(b"qa-test", delivery, "sha256").hex()}
            self.assertEqual(post("/hooks/hackerone", delivery, headers), 200)
            self.assertEqual(len(graph.cases()), 1)
            handoffs = Handoffs()
            handoffs.enqueue(graph, CASE["id"], "owner-review")
            lease = handoffs.claim("qa-worker", 120)
            args = (lease["id"], "qa-worker", lease["token"])
            self.assertEqual(handoffs.case_context(graph, *args, metadata_only=True), {"report": CASE})
            with self.assertRaises(ReviewContextRequired):
                handoffs.finish(*args, disposition="needs-owner-review", graph=graph)
            self.assertEqual(len(objects), 1)
            readme = next(iter(objects.values()))
            self.assertNotIn(b"private provider narrative", readme)
            self.assertIn(b"Archive | retention", readme)
            out = root / "README.md"
            cmd = [sys.executable, str(ROOT / "cli.py"), "export-readme", CASE["id"], "--out", str(out)]
            subprocess.run(cmd, check=True, capture_output=True)
            self.assertEqual(out.read_bytes(), readme)
            self.assertEqual(out.stat().st_mode & 0o777, 0o600)
            self.assertNotEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
            frames = [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-11-25"},
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "get_case_context",
                        "arguments": {"case_id": CASE["id"], "format": "markdown"},
                    },
                },
            ]
            run = subprocess.run(
                [sys.executable, str(ROOT / "cli.py"), "mcp"],
                capture_output=True,
                check=True,
                input=b"".join(json.dumps(f).encode() + b"\n" for f in frames),
            )
            self.assertEqual(
                json.loads(run.stdout.splitlines()[-1])["result"]["content"][0]["text"], readme.decode()
            )

    @unittest.skipUnless(importlib.util.find_spec("boto3"), "optional S3 client")
    def test_sdk_signs_conditional_put_and_accepts_only_existing_key_on_replay(self):
        calls = []
        statuses = iter((200, 412, 403))

        class Bucket(BaseHTTPRequestHandler):
            def do_PUT(self):
                calls.append((self.path, self.headers, self.rfile.read(int(self.headers["Content-Length"]))))
                status = next(statuses)
                errors = {412: "PreconditionFailed", 403: "AccessDenied"}
                reply = f"<Error><Code>{errors[status]}</Code></Error>".encode() if status in errors else b""
                self.send_response(status)
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)

            def log_message(self, *args):
                pass

        with HTTPServer(("127.0.0.1", 0), Bucket) as bucket:
            thread = Thread(target=bucket.serve_forever, daemon=True)
            thread.start()
            env = dict(
                ICHNIX_ARCHIVE_BUCKET="qa-example",
                AWS_DEFAULT_REGION="us-east-1",
                ICHNIX_ARCHIVE_ENDPOINT=f"http://127.0.0.1:{bucket.server_port}",
                AWS_ACCESS_KEY_ID="example",
                AWS_SECRET_ACCESS_KEY="example",
            )
            try:
                data = report_context.report_readme(CASE).encode()
                with patch.dict(os.environ, env, clear=True):
                    report_context.archive_readme(data)
                    report_context.archive_readme(data)
                    with self.assertRaises(RuntimeError):
                        report_context.archive_readme(data)
                    for unsafe in (
                        "http://storage.example",
                        "https://user@storage.example",
                        "https://storage.example/?key",
                    ):
                        with (
                            patch.dict(os.environ, ICHNIX_ARCHIVE_ENDPOINT=unsafe),
                            self.assertRaises(RuntimeError),
                        ):
                            report_context.archive_readme(data)
                self.assertEqual(len(calls), 3)
                for path, headers, body in calls:
                    self.assertEqual(
                        path, "/qa-example/reports/" + hashlib.sha256(data).hexdigest() + "/README.md"
                    )
                    self.assertEqual(headers["If-None-Match"], "*")
                    self.assertTrue(headers["Authorization"].startswith("AWS4-HMAC-SHA256 "))
                    self.assertEqual(body, data)
            finally:
                bucket.shutdown()
                thread.join()

    @unittest.skipUnless(
        importlib.util.find_spec("anydoc") and sys.platform.startswith("linux"), "optional Linux AnyDoc"
    )
    def test_offline_document_conversion(self):
        with TemporaryDirectory() as tmp:
            source, out = (Path(tmp) / p for p in ("case.csv", "README.md"))
            source.write_text("Case,Team\nExample,Records\n")
            cmd = [sys.executable, str(ROOT / "cli.py"), "document-readme", str(source), "--out", str(out)]
            subprocess.run(cmd, check=True, capture_output=True)
            self.assertIn("| Example | Records |", out.read_text())
            self.assertEqual(out.stat().st_mode & 0o777, 0o600)
            self.assertNotEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
            bundle = Path(tmp) / "case.zip"
            subprocess.run([*cmd[:-1], str(bundle), "--bundle"], check=True, capture_output=True)
            self.assertEqual(bundle.stat().st_mode & 0o777, 0o600)
            with ZipFile(bundle) as archive:
                self.assertEqual(archive.namelist(), ["README.md", "original.csv"])
                self.assertEqual(archive.read("original.csv"), source.read_bytes())
                self.assertEqual(archive.read("README.md"), out.read_bytes())

    @unittest.skipUnless(importlib.util.find_spec("anydoc") and sys.platform.startswith("linux"), "optional Linux AnyDoc")
    def test_bundle_keeps_embedded_image_with_original_document(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "evidence.docx"
            image = (ROOT / "public/evidence-graph.png").read_bytes()
            with ZipFile(path, "w") as document:
                document.writestr("[Content_Types].xml",
                    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                    '<Default Extension="png" ContentType="image/png"/>'
                    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                    'wordprocessingml.document.main+xml"/></Types>')
                document.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="doc" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
                    'officeDocument" Target="word/document.xml"/></Relationships>')
                document.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                    'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Approved staging evidence</w:t></w:r></w:p>'
                    '</w:body></w:document>')
                document.writestr("word/media/diagram.png", image)
            with ZipFile(BytesIO(report_context.convert_document(path, bundle=True))) as archive:
                self.assertIn(b"Approved staging evidence", archive.read("README.md"))
                original = archive.read("original.docx")
                self.assertEqual(original, path.read_bytes())
                with ZipFile(BytesIO(original)) as document:
                    self.assertEqual(document.read("word/media/diagram.png"), image)
