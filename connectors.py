"""Read-only bounty platform intake with customer-owned asset mappings."""

import base64
import hashlib
import hmac
import json
import os
import re
from uuid import UUID
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

REVIEWED = {"triaged", "retesting", "resolved"}
SCOPE_FIELDS = ("service", "revision", "component", "owner")


def _text(value, limit=160):
    if not isinstance(value, str) or not 0 < len(value.strip()) <= limit:
        raise ValueError("invalid metadata field")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("control character in metadata")
    return value.strip()


def _program(program, catalog):
    if not isinstance(program, str) or not re.fullmatch(r"[\w.-]{1,80}", program, re.ASCII):
        raise ValueError("invalid program handle")
    if not isinstance(catalog, dict) or catalog.get("program") != program:
        raise ValueError("catalog must name the configured program")
    for name in ("reports", "assets"):
        if not isinstance(catalog.get(name, {}), dict):
            raise ValueError("invalid inventory mapping")


def _relationship(report, key):
    relations = report.get("relationships", {})
    link = relations.get(key) if isinstance(relations, dict) else None
    item = link.get("data") if isinstance(link, dict) else None
    attrs = item.get("attributes") if isinstance(item, dict) else None
    return attrs if isinstance(attrs, dict) else {}


def _import(report, program, catalog, graph):
    if not isinstance(report, dict) or report.get("type") != "report":
        raise ValueError("expected report resource")
    number = report.get("id")
    if not isinstance(number, str) or not re.fullmatch(r"[0-9]{1,20}", number):
        raise ValueError("invalid source report id")
    attrs = report.get("attributes")
    if not isinstance(attrs, dict):
        raise ValueError("invalid report attributes")
    if attrs.get("state") not in REVIEWED:
        return False
    source_program = _relationship(report, "program").get("handle")
    if source_program and source_program != program:
        raise PermissionError("report belongs to another program")
    asset = _relationship(report, "structured_scope").get("asset_identifier")
    mapping = catalog.get("reports", {}).get(number) or catalog.get("assets", {}).get(asset)
    if mapping is None:
        return False
    if not isinstance(mapping, dict):
        raise ValueError("invalid asset metadata")
    record = {key: _text(mapping.get(key)) for key in SCOPE_FIELDS}
    record.update(id=f"h1-{program}-{number}", title=_text(attrs.get("title"), 240))
    graph.import_report(record)
    return True


def process_webhook(body, headers, secret, program, catalog, graph):
    if not secret:
        raise RuntimeError("webhook signing secret is required")
    if len(body) > 1024 * 1024:
        raise ValueError("webhook exceeds one MiB")
    _program(program, catalog)
    headers = {k.lower(): v for k, v in headers.items()}
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    supplied = headers.get("x-h1-signature", "")
    if not isinstance(supplied, str) or not hmac.compare_digest(expected.encode(), supplied.encode()):
        raise PermissionError("invalid webhook signature")
    event = json.loads(body)
    if not isinstance(event, dict) or not isinstance(event.get("data"), dict):
        raise ValueError("invalid webhook payload")
    report = event["data"].get("report")
    imported = bool(report) and _import(report, program, catalog, graph)
    return {"imported": int(imported), "skipped": int(not imported)}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _page(opener, source, url, headers):
    try:
        with opener.open(Request(url, headers=headers), timeout=15) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
    except HTTPError as exc:
        raise RuntimeError(f"{source} returned HTTP {exc.code}") from None
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"{source} connection unavailable") from exc
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("report page exceeds size limit")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError(f"expected a {source} reports page")
    return payload


def sync(program, catalog, graph):
    _program(program, catalog)
    user = os.getenv("HACKERONE_API_USER")
    token = os.getenv("HACKERONE_API_TOKEN")
    if not user or not token:
        raise RuntimeError("set HACKERONE_API_USER and HACKERONE_API_TOKEN")
    auth = base64.b64encode(f"{user}:{token}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}
    opener = build_opener(_NoRedirect())
    imported = skipped = 0
    seen = set()
    for page in range(1, 10001):
        query = urlencode({"filter[program][]": program, "filter[state][]": sorted(REVIEWED),
                           "page[number]": page, "page[size]": 25}, doseq=True)
        payload = _page(opener, "HackerOne", "https://api.hackerone.com/v1/reports?" + query, headers)
        reports = payload["data"]
        for report in reports:
            if _import(report, program, catalog, graph):
                imported += 1
            else:
                skipped += 1
        if not (payload.get("links") or {}).get("next"):
            return {"imported": imported, "skipped": skipped, "pages": page}
        fingerprint = tuple(r["id"] for r in reports)
        if fingerprint in seen or not reports:
            raise RuntimeError("HackerOne pagination did not advance")
        seen.add(fingerprint)
    raise RuntimeError("report page limit exceeded")


def _bugcrowd_submission(submission, engagements, catalog, graph):
    if submission.get("type") != "submission":
        raise ValueError("expected a Bugcrowd submission")
    case_id = str(UUID(submission["id"]))
    attrs = submission["attributes"]
    if attrs["state"] not in {"triaged", "unresolved", "resolved"} or attrs.get("duplicate"):
        return False
    relations = submission["relationships"]
    engagement_id = relations["engagement"]["data"]["id"]
    if engagement_id not in engagements:
        raise PermissionError("submission is outside the configured engagement")
    target = (relations.get("target", {}).get("data") or {}).get("id")
    mapping = catalog.get("reports", {}).get(case_id) or catalog.get("assets", {}).get(target)
    if mapping is None:
        return False
    if not isinstance(mapping, dict):
        raise ValueError("invalid inventory metadata")
    record = {key: _text(mapping.get(key)) for key in SCOPE_FIELDS}
    record.update(id=f"bc-{case_id}", title=_text(attrs.get("title"), 240))
    graph.import_report(record)
    return True


def sync_bugcrowd(engagement, catalog, graph):
    _program(engagement, catalog)
    token = os.getenv("BUGCROWD_API_TOKEN")
    if not token or ":" not in token:
        raise RuntimeError("set BUGCROWD_API_TOKEN to token-id:token-secret")
    headers = {
        "Authorization": f"Token {token}",
        "Accept": "application/vnd.bugcrowd+json",
        "Bugcrowd-Version": "2024-03-27",
    }
    params = {
        "filter[engagement]": engagement,
        "filter[state]": "triaged,unresolved,resolved",
        "include": "engagement",
        "fields[engagement]": "code",
        "fields[submission]": "title,state,engagement,target,duplicate",
        "page[limit]": 25,
    }
    opener = build_opener(_NoRedirect())
    seen = set()
    imported = skipped = 0
    for page in range(10000):
        params["page[offset]"] = 25 * page
        payload = _page(opener, "Bugcrowd", "https://api.bugcrowd.com/submissions?" + urlencode(params), headers)
        engagements = {item["id"] for item in payload.get("included", [])
                       if item.get("type") == "engagement" and item.get("attributes", {}).get("code") == engagement}
        submissions = payload["data"]
        fingerprint = tuple(row["id"] for row in submissions)
        if fingerprint in seen:
            raise RuntimeError("Bugcrowd pagination did not advance")
        seen.add(fingerprint)
        for row in submissions:
            if _bugcrowd_submission(row, engagements, catalog, graph):
                imported += 1
            else:
                skipped += 1
        if not (payload.get("links") or {}).get("next"):
            return {"imported": imported, "skipped": skipped, "pages": page + 1}
        if not submissions:
            raise RuntimeError("empty continuation page")
    raise RuntimeError("report page limit exceeded")
