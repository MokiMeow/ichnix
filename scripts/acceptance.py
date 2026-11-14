"""Exercise public disclosure intake, signed local staging observations, and scoped worker review."""

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch

from bench_workers import Client, ROOT
from eval_graph import isolated_graph
from server import ConsoleServer, Handler

SOURCE = "https://hackerone.com/reports/2236133"
CASE = "h1-curl-2236133"
SERVICE = "local-acceptance-console"
REVIEWER = "acceptance-reviewer"
HEADERS = {"cache_control": ("Cache-Control", "no-store"),
           "content_type_options": ("X-Content-Type-Options", "nosniff"),
           "referrer_policy": ("Referrer-Policy", "no-referrer")}


def cli(*args, output=True):
    result = subprocess.run([sys.executable, str(ROOT / "cli.py"), *map(str, args)],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(f"CLI {args[0]} failed (exit {result.returncode})")
    return json.loads(result.stdout) if output else None


def api(port, method, route, credential, body=None):
    connection = HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        headers = {"Authorization": "Bearer " + credential.read_text().strip(), "Content-Type": "application/json"}
        connection.request(method, route, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f"local {method} request returned {response.status}")
        return json.load(response), response.headers
    finally:
        connection.close()


def worker_review(port, token_file, job_id, events):
    env = {"PATH": os.environ["PATH"], "ICHNIX_WORKSPACE_URL": f"http://127.0.0.1:{port}",
           "ICHNIX_WORKER_TOKEN_FILE": str(token_file)}
    with subprocess.Popen([sys.executable, str(ROOT / "cli.py"), "mcp", "--worker"],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, text=True) as proc:
        client = Client(proc)
        client.initialize()
        job = client.tool("claim_review")
        if not job or job["id"] != job_id or job["kind"] != "evidence-review" or job["case_id"] != CASE:
            raise RuntimeError("worker received unexpected lease")
        context = client.tool("get_review_context", job_id=job_id)
        if context["report"]["id"] != CASE or context["evidence"] != events:
            raise RuntimeError("worker read different case evidence")
        if not any(row["owner"] == REVIEWER for row in context["recommendations"]):
            raise RuntimeError("expected remediation owner missing from worker context")
        completed = client.tool("finish_review", job_id=job_id)
        if completed["state"] != "complete" or client.tool("claim_review") is not None:
            raise RuntimeError("evidence handoff did not complete")


def acceptance(folder, port):
    revision = "local-acceptance-v1"
    mapping = {"service": SERVICE, "component": "local-http-response", "revision": revision, "owner": REVIEWER}
    catalog = folder / "catalog.json"
    catalog.write_text(json.dumps({"program": "curl", "reports": {"2236133": mapping}}))
    imported = cli("import-disclosed", "--program", "curl", "--report-id", "2236133", "--catalog", catalog)
    if imported.get("imported") != 1 or imported.get("source") != SOURCE:
        raise RuntimeError("public disclosure did not enter the isolated workspace")

    worker, reviewer = folder / "worker.token", folder / "reviewer.token"
    cli("identity", "issue", "acceptance-worker", "--role", "worker", "--out", worker, output=False)
    cli("identity", "issue", REVIEWER, "--role", "review", "--out", reviewer, output=False)
    rows, headers = api(port, "GET", "/api/cases", reviewer)
    if len(rows) != 1 or rows[0]["id"] != CASE or any(rows[0][key] != value for key, value in mapping.items()):
        raise RuntimeError("case mapping or workspace isolation check failed")
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    settings = {key: headers[header] for key, (header, _) in HEADERS.items() if header in headers}
    manifest = folder / "manifest.json"
    manifest.write_bytes(json.dumps({"environment": "staging", "service": SERVICE, "revision": revision,
                                    "observed_at": observed_at, "settings": settings}, sort_keys=True).encode())
    policy = folder / "policy.json"
    policy.write_text(json.dumps({"services": {SERVICE: {key: value for key, (_, value) in HEADERS.items()}}}))
    signature = folder / "ci.sig"
    cli("staging", "--sign", "--manifest", manifest, "--out", signature, output=False)
    events = cli("staging", CASE, "--manifest", manifest, "--signature", signature, "--policy", policy)
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    observed = {event["step"]: event["result"] for event in events}
    if len(events) != len(HEADERS) or observed != dict.fromkeys(HEADERS, "passed") or any(
            e["source_digest"] != digest or e["source_method"] != "hmac-sha256" for e in events):
        raise RuntimeError("signed observations differ from approved local controls")
    job_id = cli("handoff", "enqueue", CASE, "evidence-review")["id"]
    worker_review(port, worker, job_id, events)
    persisted = cli("handoff", "status", job_id)
    if persisted["worker"] != "acceptance-worker" or persisted["state"] != "complete" or persisted["attempts"] != 1:
        raise RuntimeError("completed worker receipt could not be verified")
    api(port, "POST", f"/api/cases/{CASE}/reviews", reviewer, {"owner": REVIEWER, "accepted": True})
    saved = cli("context", CASE)
    if not any(row["actor"] == REVIEWER and row["owner"] == REVIEWER and row["accepted"] is True
               for row in saved["review_history"]):
        raise RuntimeError("review decision missing from graph history")
    return {"source": SOURCE, "case_id": CASE, "scope": "local Ichnix acceptance deployment",
            "observed_at": observed_at, "manifest_sha256": digest, "source_method": "hmac-sha256",
            "observations": observed, "handoff_state": persisted["state"], "review_recorded": True}


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    with TemporaryDirectory() as temp, isolated_graph():
        folder = Path(temp)
        key = folder / "ci.key"
        with os.fdopen(os.open(key, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as stream:
            stream.write(secrets.token_bytes(32))
        with patch.dict(os.environ, ICHNIX_IDENTITY_DB=str(folder / "registry.sqlite"),
                        ICHNIX_CI_HMAC_KEY_FILE=str(key)), ConsoleServer(("127.0.0.1", 0), Handler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            with ExitStack() as cleanup:
                cleanup.callback(thread.join)
                cleanup.callback(server.shutdown)
                receipt = acceptance(folder, server.server_port)
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"acceptance failed: {exc}", file=sys.stderr)
        sys.exit(1)
