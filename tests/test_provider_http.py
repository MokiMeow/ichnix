import hashlib
import hmac
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import Request
from uuid import uuid4

from ichnix import connectors
from ichnix.eval_graph import isolated_graph
from ichnix.server import ConsoleServer, Handler


class ProviderHTTPTests(unittest.TestCase):
    def test_verified_deliveries_import_only_reviewed_metadata(self):
        scope = dict(service="ledger", component="archive", revision="r7", owner="records")
        submission = str(uuid4())
        secret = secrets.token_hex()
        api_key = "fixture:" + secrets.token_hex()
        upstream_requests = []

        class BugcrowdAPI(BaseHTTPRequestHandler):
            def do_GET(self):
                upstream_requests.append((urlsplit(self.path).path, self.headers.get("Authorization") == "Token " + api_key))
                self.send_response(200)
                self.end_headers()
                record = {"id": submission, "type": "submission", "attributes": {
                    "state": "triaged", "title": "Inventory retention", "description": "fixture-private-body"},
                    "relationships": {"engagement": {"data": {"id": "scope-1"}}}}
                self.wfile.write(json.dumps({"data": record, "included": [{"id": "scope-1", "type": "engagement",
                                            "attributes": {"code": "fixture"}}]}).encode())

            def log_message(self, *args):
                pass

        with isolated_graph() as db, TemporaryDirectory() as tmp, HTTPServer(("127.0.0.1", 0), BugcrowdAPI) as upstream:
            catalog = Path(tmp) / "catalog.json"
            catalog.write_text(json.dumps({"program": "fixture", "reports": {submission: scope, "501": scope}}))
            settings = dict(ICHNIX_PROGRAM="fixture", ICHNIX_CATALOG=str(catalog), ICHNIX_WEBHOOK_SECRET=secret,
                            ICHNIX_BUGCROWD_ENGAGEMENT="fixture", ICHNIX_BUGCROWD_CATALOG=str(catalog),
                            ICHNIX_BUGCROWD_WEBHOOK_SECRET=secret, BUGCROWD_API_TOKEN=api_key)

            def provider_request(url, headers):
                parts = urlsplit(url)
                self.assertEqual((parts.scheme, parts.netloc), ("https", "api.bugcrowd.com"))
                return Request(f"http://127.0.0.1:{upstream.server_port}{parts.path}?{parts.query}", headers=headers)

            with patch.dict(os.environ, settings), patch.object(connectors, "Request", provider_request), ConsoleServer(
                    ("127.0.0.1", 0), Handler) as console:
                threads = [Thread(target=server.serve_forever, daemon=True) for server in (console, upstream)]
                for thread in threads:
                    thread.start()

                def post(provider, payload, *, tamper=False):
                    body = json.dumps(payload).encode()
                    if provider == "bugcrowd":
                        stamp = "1718900000"
                        digest = hmac.digest(secret.encode(), body + stamp.encode(), hashlib.sha256).hex()
                        header = {"X-Bugcrowd-Digest": f"timestamp={stamp};sha256={digest}"}
                    else:
                        header = {"X-H1-Signature": "sha256=" + hmac.digest(secret.encode(), body, hashlib.sha256).hex()}
                    conn = HTTPConnection(*console.server_address, timeout=10)
                    try:
                        conn.request("POST", "/hooks/" + provider, body + b" " if tamper else body, header)
                        response = conn.getresponse()
                        return response.status, json.load(response)
                    finally:
                        conn.close()

                try:
                    h1 = {"id": "501", "type": "report", "attributes": {"state": "triaged", "title": "invalid\ud800",
                          "vulnerability_information": "fixture-private-body"}}
                    payload = {"data": {"report": h1}}
                    self.assertEqual(post("hackerone", payload)[0], 400)
                    self.assertEqual(db._query("MATCH (s:Snapshot) RETURN s.id AS id"), [])
                    h1["attributes"]["title"] = "Archive ownership"
                    event = {"data": {"type": "event", "attributes": {"key": "submission.updated"},
                             "relationships": {"resource": {"data": {"type": "submission", "id": submission}}}}}
                    for provider, body in (("bugcrowd", event), ("hackerone", payload)):
                        self.assertEqual(post(provider, body, tamper=True)[0], 403)
                    self.assertEqual(upstream_requests, [])
                    for provider, body in (("bugcrowd", event), ("hackerone", payload)):
                        for _ in range(2):
                            self.assertEqual(post(provider, body), (200, {"imported": 1, "skipped": 0}))
                    self.assertEqual(upstream_requests, [("/submissions/" + submission, True)] * 2)
                    self.assertEqual(len(db.cases()), 2)
                    for key, title in (("bc-" + submission, "Inventory retention"), ("h1-fixture-501", "Archive ownership")):
                        context = db.context(key)
                        self.assertEqual(context["report"], dict(scope, id=key, title=title))
                        self.assertNotIn("fixture-private-body", json.dumps(context))
                finally:
                    console.shutdown()
                    upstream.shutdown()
                    for thread in threads:
                        thread.join()
