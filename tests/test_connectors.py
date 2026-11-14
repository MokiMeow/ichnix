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

    def test_disclosed_import_requires_public_scope_and_projects_only_metadata(self):
        record = {"id": 17, "public": True, "disclosed_at": "2024-01-02", "substate": "resolved",
                  "title": "Reviewed configuration", "team": {"handle": "fixture"},
                  "structured_scope": {"asset_identifier": "ledger"}, "attachments": ["private"]}
        catalog = {"program": "fixture", "assets": {"ledger": self.asset}}
        with patch.object(connectors, "_fetch", return_value=record):
            self.assertEqual(connectors.import_disclosed("fixture", "17", catalog, self.graph)["imported"], 1)
            record["public"] = False
            with self.assertRaises(PermissionError):
                connectors.import_disclosed("fixture", "17", catalog, self.graph)
            record.update(public=True, team={"handle": "different"})
            with self.assertRaises(PermissionError):
                connectors.import_disclosed("fixture", "17", catalog, self.graph)
        self.graph.import_report.assert_called_once_with(dict(self.asset, title="Reviewed configuration",
                                                             id="h1-fixture-17"))

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


    def test_bugcrowd_delivery_refetches_authoritative_submission(self):
        case, engagement = str(uuid4()), str(uuid4())
        secret = secrets.token_hex()
        body = json.dumps({"data": {"type": "event", "attributes": {"key": "submission.updated"},
                          "relationships": {"resource": {"data": {"type": "submission", "id": case}}}}}).encode()
        at = "1718900000"
        signature = hmac.new(secret.encode(), body + at.encode(), hashlib.sha256).hexdigest()
        headers = {"X-Bugcrowd-Digest": f"timestamp={at};sha256={signature}"}
        catalog = {"program": "sample", "reports": {case: self.asset}}
        included = [{"type": "engagement", "id": engagement, "attributes": {"code": "sample"}}]
        record = {"id": case, "type": "submission", "attributes": {"state": "triaged", "title": "Sample"},
                  "relationships": {"engagement": {"data": {"id": engagement}}}}
        with patch.object(connectors, "_fetch", return_value={"included": included, "data": record}) as fetch:
            with self.assertRaises(PermissionError):
                connectors.process_bugcrowd_webhook(body + b" ", headers, secret, "sample", catalog, self.graph)
            fetch.assert_not_called()
            with patch.dict(os.environ, BUGCROWD_API_TOKEN="test:" + secrets.token_hex()):
                result = connectors.process_bugcrowd_webhook(body, headers, secret, "sample", catalog, self.graph)
                self.assertEqual(result["imported"], 1)
                self.assertEqual(urlsplit(fetch.call_args.args[2]).path, "/submissions/" + case)
                included[0]["attributes"]["code"] = "another-program"
                with self.assertRaises(PermissionError):
                    connectors.process_bugcrowd_webhook(body, headers, secret, "sample", catalog, self.graph)
        self.graph.import_report.assert_called_once_with(dict(self.asset, title="Sample", id="bc-" + case))


if __name__ == "__main__":
    unittest.main()
