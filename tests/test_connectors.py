import hashlib
import hmac
import io
import json
import os
import secrets
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import connectors


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.graph = Mock()
        self.asset = {"service": "checkout", "component": "policy", "owner": "platform", "revision": "v12"}

    def test_signed_report_is_projected_and_tampering_rejected(self):
        signed = secrets.token_hex(32)
        catalog = {"program": "fixture", "reports": {"5": self.asset}}
        report = {"id": "5", "type": "report", "attributes": {"title": "Sample", "state": "triaged",
                                                                "vulnerability_information": "private details"}}
        body = json.dumps({"data": {"report": report}}).encode()
        signature = hmac.new(signed.encode(), body, hashlib.sha256).hexdigest()
        headers = {"X-H1-Signature": "sha256=" + signature}
        self.assertEqual(connectors.process_webhook(body, headers, signed, "fixture", catalog, self.graph)["imported"], 1)
        with self.assertRaises(PermissionError):
            connectors.process_webhook(body + b" ", headers, signed, "fixture", catalog, self.graph)
        self.graph.import_report.assert_called_once_with(dict(self.asset, title="Sample", id="h1-fixture-5"))

    def test_scoped_bugcrowd_sync_uses_local_pagination(self):
        engagement, target = str(uuid4()), str(uuid4())
        catalog = {"program": "fixture", "assets": {target: self.asset}}
        included = [{"type": "engagement", "id": engagement, "attributes": {"code": "fixture"}}]
        item = {"type": "submission", "id": str(uuid4()), "attributes": {"state": "resolved", "title": "Sample"},
                "relationships": {"engagement": {"data": {"id": engagement}}, "target": {"data": {"id": target}}}}
        page_one = {"data": [item], "included": included, "links": {"next": "https://elsewhere.invalid/"}}
        page_two = {"data": [], "links": {}}
        opener = Mock()
        opener.open.side_effect = [io.BytesIO(json.dumps(p).encode()) for p in (page_one, page_two)]
        with patch.dict(os.environ, BUGCROWD_API_TOKEN="fixture:" + secrets.token_hex()), patch.object(connectors, "build_opener", return_value=opener):
            result = connectors.sync_bugcrowd("fixture", catalog, self.graph)
        for i, call in enumerate(opener.open.call_args_list):
            request = call.args[0]
            parts = urlsplit(request.full_url)
            self.assertEqual((request.method if hasattr(request, "method") else request.get_method(), parts.hostname), ("GET", "api.bugcrowd.com"))
            args = parse_qs(parts.query)
            self.assertEqual(args["filter[engagement]"], ["fixture"])
            self.assertEqual(args["page[offset]"], [str(i * 25)])
        self.assertEqual(result, {"pages": 2, "imported": 1, "skipped": 0})
        self.graph.import_report.assert_called_once_with(dict(self.asset, title="Sample", id="bc-" + item["id"]))
        with self.assertRaises(PermissionError):
            connectors._bugcrowd_submission(item, set(), catalog, self.graph)


if __name__ == "__main__":
    unittest.main()
