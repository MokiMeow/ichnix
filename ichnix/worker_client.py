"""Private HTTP transport for a single remote review worker session."""

from http.client import HTTPConnection, HTTPSConnection, HTTPException
from ipaddress import ip_address
import json
import os
import re
import stat
import time
from urllib.parse import urlsplit

from ichnix.contracts import DISPOSITIONS
from ichnix.report_context import report_readme


JOB_FIELDS = ("id", "kind", "case_id", "service", "component", "revision", "state", "expires")
JOB_ID = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
MAX_RESPONSE = 1024 * 1024


def _tool(name, description, properties, required=(), read_only=False):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties,
                            "required": list(required), "additionalProperties": False},
            "annotations": {"readOnlyHint": read_only, "destructiveHint": False, "openWorldHint": False}}


TOOLS = [
    _tool("claim_review", "Lease the next queued evidence or owner review. Returns null when the queue is empty.",
          {"lease_seconds": {"type": "integer", "minimum": 1, "maximum": 3600}}),
    _tool("get_review_context", "Read the leased case and evidence, or preview just the mapped metadata as Markdown.",
          {"job_id": JOB_ID, "format": {"type": "string", "enum": ["context", "markdown"]}}, ["job_id"], read_only=True),
    _tool("finish_review", "Complete this lease. After reading its context, optionally record a disposition.",
          {"job_id": JOB_ID, "disposition": {"type": "string",
                                           "enum": [d for values in DISPOSITIONS.values() for d in values]}},
          ["job_id"]),
]


def workspace_origin(value, *, hosted=False):
    if not isinstance(value, str) or not value.isascii() or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        raise ValueError("workspace origin required")
    parts = urlsplit(value)
    if (parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.path not in ("", "/") or parts.query or parts.fragment):
        raise ValueError("invalid workspace origin")
    if hosted and parts.scheme != "https":
        raise ValueError("hosted workspace requires HTTPS")
    if parts.scheme == "http" and parts.hostname != "localhost":
        try:
            loopback = ip_address(parts.hostname).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError("workspace requires HTTPS")
    return parts


def _credential(path, token_env):
    if token_env is not None:
        if path or token_env not in ("ICHNIX_WORKER_TOKEN", "ICHNIX_USER_TOKEN"):
            raise ValueError("expected one named credential source")
        secret = os.getenv(token_env, "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,512}", secret):
            raise ValueError("missing or invalid environment credential")
        return secret
    if os.name == "nt":
        raise ValueError("Windows desktop clients require an inherited token environment variable")
    if not path:
        raise ValueError("workspace token file required")
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        mode = os.fstat(fd).st_mode
        if not stat.S_ISREG(mode) or mode & 0o077:
            raise ValueError("workspace token requires a private regular file")
        raw = stream.read(514)
    if len(raw) > 513 or not re.fullmatch(rb"[A-Za-z0-9_-]{32,512}\n?", raw):
        raise ValueError("invalid workspace credential")
    return raw.rstrip(b"\n").decode("ascii")


class WorkspaceClient:
    def __init__(self, url, token_file=None, *, hosted=False, token_env=None):
        origin = workspace_origin(url, hosted=hosted)
        self.hosted = hosted
        self.connection = HTTPSConnection if origin.scheme == "https" else HTTPConnection
        self.host, self.port = origin.hostname, origin.port
        self.bearer = _credential(token_file, token_env)

    def request(self, method, path, body=None, *, request_id=None):
        # http.client never follows redirects or sends credentials through environment proxies.
        if self.hosted:
            if not path.startswith("/api/"):
                raise ValueError("expected a workspace API route")
            path = "/org-api/" + path[len("/api/"):]
        connection = self.connection(self.host, self.port, timeout=15)
        try:
            headers = {"Authorization": "Bearer " + self.bearer}
            payload = None
            if body is not None:
                headers["Content-Type"] = "application/json"
                payload = json.dumps(body).encode()
            if request_id:
                headers["Idempotency-Key"] = request_id
            connection.request(method, path, payload, headers)
            response = connection.getresponse()
            if response.status in (401, 403):
                raise PermissionError("workspace access denied")
            if response.status != 200 or response.headers.get_content_type() != "application/json":
                raise RuntimeError("workspace request failed")
            payload = response.read(MAX_RESPONSE + 1)
            if len(payload) > MAX_RESPONSE:
                raise RuntimeError("workspace response too large")
            return json.loads(payload)
        except (OSError, HTTPException) as error:
            if isinstance(error, PermissionError):
                raise
            raise RuntimeError("workspace unavailable") from None
        finally:
            connection.close()

    def auth_status(self):
        data = self.request("GET", "/api/auth/status")
        if (not isinstance(data, dict) or set(data) != {"name", "role"}
                or data["role"] not in ("read", "review", "intake", "worker")
                or not isinstance(data["name"], str)
                or not re.fullmatch(r"[a-zA-Z0-9_.@-]{1,80}|shared:(?:read|review)", data["name"])
                or self.bearer in data["name"]):
            raise PermissionError("invalid workspace identity")
        return data


class Worker(WorkspaceClient):
    def __init__(self, url, token_file=None, *, hosted=False, token_env=None):
        super().__init__(url, token_file, hosted=hosted, token_env=token_env)
        self.lease = None
        self.completed = None

    def verify(self):
        if self.auth_status()["role"] != "worker":
            raise PermissionError("worker credential required")

    def _post(self, path, body):
        try:
            return self.request("POST", path, body)
        except PermissionError:
            self.lease = None
            raise

    def call(self, name, args):
        if name == "claim_review":
            if set(args) - {"lease_seconds"}:
                raise ValueError("unknown claim argument")
            duration = args.get("lease_seconds", 120)
            if type(duration) is not int or not 1 <= duration <= 3600:
                raise ValueError("invalid lease length")
            if self.lease and self.lease["expires"] > time.time():
                raise RuntimeError("this session already holds a lease")
            self.lease = None
            job = self._post("/api/handoffs/claim", {"lease_seconds": duration})
            if job is None:
                return None
            if (not isinstance(job, dict) or not isinstance(job.get("id"), str)
                    or not re.fullmatch(JOB_ID["pattern"], job["id"])
                    or job.get("kind") not in ("owner-review", "evidence-review")
                    or type(job.get("expires")) is not int or job.get("state") != "leased"
                    or not isinstance(job.get("token"), str) or not 1 <= len(job["token"]) <= 256):
                raise RuntimeError("invalid lease response")
            public = {key: job[key] for key in JOB_FIELDS}
            self._check_private(public, job["token"])
            self.lease = job
            return public

        allowed = ({"job_id"}, {"job_id", "disposition"}) if name == "finish_review" else ({"job_id"}, {"job_id", "format"})
        if name not in ("get_review_context", "finish_review") or set(args) not in allowed:
            raise ValueError("unknown tool or invalid arguments")
        lease = self.lease
        if name == "finish_review" and not lease:
            lease = self.completed
        if not lease or lease["id"] != args["job_id"]:
            raise PermissionError("this session does not hold that lease")
        payload = {"token": lease["token"]}
        if "format" in args:
            if args["format"] not in ("context", "markdown"):
                raise ValueError("invalid case format")
            if args["format"] == "markdown":
                payload["format"] = "markdown"
        if "disposition" in args:
            if args["disposition"] not in DISPOSITIONS[lease["kind"]]:
                raise ValueError("invalid disposition for this review")
            payload["disposition"] = args["disposition"]
        action = "context" if name == "get_review_context" else "finish"
        data = self._post(f"/api/handoffs/{lease['id']}/{action}", payload)
        if not isinstance(data, dict):
            raise RuntimeError("invalid workspace response")
        if action == "context":
            report = data.get("report")
            if not isinstance(report, dict) or report.get("id") != lease["case_id"] or any(
                    report.get(key) != lease[key] for key in ("service", "component", "revision")):
                raise RuntimeError("case mapping differs from the active lease")
            data = {key: data[key] for key in ("report", "evidence", "recommendations", "ranked_checks") if key in data}
        else:
            if data.get("id") != lease["id"] or data.get("state") != "complete":
                raise RuntimeError("unexpected completion response")
            receipt = {key: data[key] for key in JOB_FIELDS}
            if "disposition" in args:
                if data.get("disposition") != args["disposition"]:
                    raise RuntimeError("unexpected review disposition")
                receipt["disposition"] = data["disposition"]
            data = receipt
            self.completed = lease
            self.lease = None
        self._check_private(data, lease["token"])
        return report_readme(data["report"]) if "format" in payload else data

    def _check_private(self, payload, lease_token):
        serialized = json.dumps(payload)
        if self.bearer in serialized or lease_token in serialized:
            raise RuntimeError("workspace returned private credential material")
