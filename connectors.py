"""Read-only HackerOne report intake with customer-owned asset mappings."""

import base64
import hashlib
import hmac
import json
import os
import re
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
        try:
            with opener.open(Request("https://api.hackerone.com/v1/reports?" + query,
                                     headers=headers), timeout=15) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
        except HTTPError as exc:
            raise RuntimeError(f"HackerOne returned HTTP {exc.code}") from None
        except (URLError, TimeoutError) as exc:
            raise RuntimeError("HackerOne connection unavailable") from exc
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("report page exceeds size limit")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError("expected a HackerOne reports page")
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
