"""Select an ordering for trusted review facts. Only coarse enums leave the workspace."""
from http.client import HTTPSConnection, HTTPException
import json
import os
import re
import stat
from urllib.parse import urlsplit

LAYOUTS = ("scope-first", "evidence-first", "owner-first")
MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"


def classify(report):
    key = report["id"]
    source = "hackerone" if key.startswith("h1-") else "bugcrowd" if key.startswith("bc-") else "local"
    words = set(re.findall(r"[a-z]+", report["title"].lower()))
    for topic, terms in (("identity", {"identity", "permission", "session", "access", "auth", "authorization"}),
                         ("data-handling", {"audit", "retention", "records", "privacy", "redaction"}),
                         ("configuration", {"configuration", "policy", "settings", "config"})):
        if words & terms:
            return {"source": source, "topic": topic}
    return {"source": source, "topic": "general"}


def local_layout(facts):
    return {"identity": "owner-first", "data-handling": "evidence-first"}.get(facts["topic"], "scope-first")


def configured_provider():
    if any(os.getenv(k) for k in ("ICHNIX_CF_ACCOUNT_ID", "ICHNIX_CF_AI_TOKEN", "ICHNIX_CF_AI_TOKEN_FILE")):
        return "workers-ai"
    if os.getenv("ICHNIX_LAYOUT_URL"):
        return "relay"
    return "local"


def _secret(name):
    file = os.getenv(name + "_FILE")
    if file:
        fd = os.open(file, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            mode = os.fstat(fd).st_mode
            if not stat.S_ISREG(mode) or mode & 0o077:
                raise ValueError("expected a private token file")
            raw = stream.read(4098)
            value = raw.decode("ascii").rstrip("\n")
    else:
        value = os.getenv(name, "")
    if not re.fullmatch(r"[\x21-\x7e]{16,4096}", value):
        raise ValueError("invalid provider credential")
    return value


def _post(host, port, path, token, body):
    # A direct TLS request cannot follow a redirect or replay credentials to another origin.
    connection = HTTPSConnection(host, port, timeout=4)
    try:
        connection.request("POST", path, json.dumps(body).encode(), {
            "Authorization": "Bearer " + token, "Content-Type": "application/json"})
        result = connection.getresponse()
        if result.status != 200:
            raise ValueError("provider request rejected")
        content = result.read(16385)
        if len(content) > 16384:
            raise ValueError("provider response too large")
        return json.loads(content)
    finally:
        connection.close()


def select_layout(facts, provider):
    fallback = local_layout(facts)
    if provider == "local":
        return fallback, "local"
    try:
        if provider == "workers-ai":
            account = os.getenv("ICHNIX_CF_ACCOUNT_ID", "")
            if not re.fullmatch("[a-fA-F0-9]{32}", account):
                raise ValueError("invalid account")
            schema = {"type": "object", "properties": {"layout": {"type": "string", "enum": list(LAYOUTS)}},
                      "required": ["layout"], "additionalProperties": False}
            body = {"messages": [{"role": "system", "content": "Choose a review diagram ordering for these enums. "
                                 "Prioritize the evidence lane for data handling, ownership for identity, or the scope "
                                 "lane for ambiguous configuration. Return only the requested JSON."},
                                 {"role": "user", "content": json.dumps(facts, sort_keys=True)}],
                    "max_tokens": 40, "temperature": 0,
                    "response_format": {"type": "json_schema", "json_schema": schema}}
            envelope = _post("api.cloudflare.com", 443, f"/client/v4/accounts/{account}/ai/run/{MODEL}",
                             _secret("ICHNIX_CF_AI_TOKEN"), body)
            if not isinstance(envelope, dict) or envelope.get("success") is not True:
                raise ValueError("provider returned failure")
            result = envelope["result"]
            if "response" in result:
                answer = result["response"]
            else:
                answer = result["choices"][0]["message"]["content"]
            if isinstance(answer, str):
                answer = json.loads(answer)
        else:
            url = urlsplit(os.environ["ICHNIX_LAYOUT_URL"])
            if (url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None
                    or url.path != "/api/report-layout" or url.query or url.fragment):
                raise ValueError("invalid HTTPS relay URL")
            answer = _post(url.hostname, url.port or 443, url.path, _secret("ICHNIX_LAYOUT_SECRET"), facts)
        if not isinstance(answer, dict) or set(answer) != {"layout"} or answer["layout"] not in LAYOUTS:
            raise ValueError("unexpected layout")
        return answer["layout"], provider
    except (ValueError, KeyError, IndexError, TypeError, OSError, HTTPException, RecursionError):
        return fallback, "local"
