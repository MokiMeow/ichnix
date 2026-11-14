"""Private HTTP transport for a single remote review worker session."""

from http.client import HTTPConnection, HTTPSConnection, HTTPException
from ipaddress import ip_address
import json
import os
import re
import stat
import time
from urllib.parse import urlsplit


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
    _tool("get_review_context", "Read the case and evidence covered by this session's active review.",
          {"job_id": JOB_ID}, ["job_id"], read_only=True),
    _tool("finish_review", "Mark this session's leased review complete after inspecting its context.",
          {"job_id": JOB_ID}, ["job_id"]),
]


def workspace_origin(value):
    if not isinstance(value, str) or not value.isascii() or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        raise ValueError("workspace origin required")
    parts = urlsplit(value)
    if (parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.path not in ("", "/") or parts.query or parts.fragment):
        raise ValueError("invalid workspace origin")
    if parts.scheme == "http" and parts.hostname != "localhost":
        try:
            loopback = ip_address(parts.hostname).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError("workspace requires HTTPS")
    return parts


def _credential(path):
    if not path:
        raise ValueError("worker token file required")
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        mode = os.fstat(fd).st_mode
        if not stat.S_ISREG(mode) or mode & 0o077:
            raise ValueError("worker token requires a private regular file")
        raw = stream.read(514)
    if len(raw) > 513 or not re.fullmatch(rb"[A-Za-z0-9_-]{32,512}\n?", raw):
        raise ValueError("invalid worker credential")
    return raw.rstrip(b"\n").decode("ascii")


class Worker:
    def __init__(self, url, token_file):
        origin = workspace_origin(url)
        self.connection = HTTPSConnection if origin.scheme == "https" else HTTPConnection
        self.host, self.port = origin.hostname, origin.port
        self.bearer = _credential(token_file)
        self.lease = None

    def _post(self, path, body):
        # http.client never follows redirects or sends credentials through environment proxies.
        connection = self.connection(self.host, self.port, timeout=15)
        try:
            connection.request("POST", path, json.dumps(body).encode(), {
                "Authorization": "Bearer " + self.bearer, "Content-Type": "application/json"})
            response = connection.getresponse()
            if response.status in (401, 403):
                self.lease = None
                raise PermissionError("review access denied or lease expired")
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

        if name not in ("get_review_context", "finish_review") or set(args) != {"job_id"}:
            raise ValueError("unknown tool or invalid arguments")
        if not self.lease or self.lease["id"] != args["job_id"]:
            raise PermissionError("this session does not hold that lease")
        lease = self.lease
        action = "context" if name == "get_review_context" else "finish"
        data = self._post(f"/api/handoffs/{lease['id']}/{action}", {"token": lease["token"]})
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
            data = {key: data[key] for key in JOB_FIELDS}
            self.lease = None
        self._check_private(data, lease["token"])
        return data

    def _check_private(self, payload, lease_token):
        serialized = json.dumps(payload)
        if self.bearer in serialized or lease_token in serialized:
            raise RuntimeError("workspace returned private credential material")
