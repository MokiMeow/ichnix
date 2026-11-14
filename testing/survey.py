"""Run the offline, synthetic multi-role workflow using a disposable Ichnix deployment."""

import argparse
from contextlib import ExitStack, contextmanager
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval_graph import isolated_graph
from mcp_server import PROTOCOL
from scripts.acceptance import cli
from scripts.bench_workers import Client, write_frame
from server import ConsoleServer, Handler

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PRIMARY = "SURVEY-101"
FOLLOWUP = "SURVEY-102"
CONTROL = "SURVEY-103"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


@contextmanager
def mcp(profile, port=None, token=None):
    args = [sys.executable, str(ROOT / "cli.py"), "mcp"]
    if profile == "worker":
        args.append("--worker")
        env = {"PATH": os.environ["PATH"], "ICHNIX_WORKSPACE_URL": f"http://127.0.0.1:{port}",
               "ICHNIX_WORKER_TOKEN_FILE": str(token)}
    else:
        env = {k: v for k, v in os.environ.items() if k.startswith("ICHNIX_HYDRA_") or k == "PATH"}
        env["no_proxy"] = "*"
    with subprocess.Popen(args, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as proc:
        client = Client(proc)
        if profile == "worker":
            client.initialize()
        else:
            negotiated = client.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                                      "clientInfo": {"name": "survey", "version": "1"}})
            require(negotiated["protocolVersion"] == PROTOCOL, "local MCP protocol negotiation failed")
            write_frame(proc.stdin, {"jsonrpc": "2.0", "method": "notifications/initialized"})
            tools = {tool["name"] for tool in client.request("tools/list")["tools"]}
            require(tools == {"list_cases", "get_case_context"}, "unexpected local MCP tools")
        yield client


def http(port, actor, route, *, payload=None, expected=200):
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        headers = {"Content-Type": "application/json"}
        if actor:
            headers["Authorization"] = "Bearer " + actor.read_text().strip()
        if payload is not None:
            headers["Idempotency-Key"] = "survey-review"
        conn.request("GET" if payload is None else "POST", route,
                     json.dumps(payload) if payload is not None else None, headers)
        response = conn.getresponse()
        require(response.status == expected, f"unexpected HTTP status {response.status} for {route}")
        return json.load(response)
    finally:
        conn.close()


def collect_events(folder):
    manifest = FIXTURES / "manifest.json"
    signature = folder / "manifest.sig"
    cli("staging", "--sign", "--manifest", manifest, "--out", signature, output=False)
    cmd = [sys.executable, str(ROOT / "cli.py"), "staging", PRIMARY, "--policy", str(FIXTURES / "policy.json"),
           "--manifest", str(manifest), "--signature", str(signature), "--run-id", "survey-build-7"]
    # A failed control returns 1 with a complete, recorded observation set.
    staged = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    require(staged.returncode == 1, "expected the signed fixture to contain a failed control")
    events = json.loads(staged.stdout)
    expected = {"retention_days": "failed", "audit_enabled": "passed", "mask_identifiers": "inconclusive"}
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    require({e["step"]: e["result"] for e in events} == expected, "unexpected staging observations")
    require(all(e["source_digest"] == digest and e["source_method"] == "hmac-sha256" for e in events),
            "missing signed provenance")
    return events, expected, digest


def run(folder, port):
    creds = {}
    for actor, role in (("survey-reader", "read"), ("survey-reviewer", "review"),
                        ("survey-owner", "worker"), ("survey-evidence", "worker")):
        creds[actor] = folder / (actor + ".token")
        cli("identity", "issue", actor, "--role", role, "--out", creds[actor], output=False)
    reader, reviewer = creds["survey-reader"], creds["survey-reviewer"]
    require(cli("import", FIXTURES / "cases.json") == {"imported": 3}, "fixture import failed")
    events, expected, digest = collect_events(folder)

    route = "/api/cases/" + PRIMARY
    review = {"owner": "records-team", "accepted": True}
    http(port, None, "/api/cases", expected=401)
    http(port, reader, route + "/reviews", payload=review, expected=401)
    http(port, reviewer, "/api/handoffs/claim", payload={}, expected=403)
    http(port, creds["survey-owner"], route, expected=401)
    require(http(port, reader, route)["evidence"] == events, "reader evidence mismatch")

    evidence = cli("handoff", "enqueue", PRIMARY, "evidence-review")
    owner = cli("handoff", "enqueue", FOLLOWUP, "owner-review")
    with mcp("worker", port, creds["survey-evidence"]) as inspector, mcp("worker", port, creds["survey-owner"]) as router:
        require(inspector.tool("claim_review")["id"] == evidence["id"], "inspector did not lease evidence")
        require(router.tool("claim_review")["id"] == owner["id"], "router did not lease owner review")
        wrong_job = router.request("tools/call", {"name": "get_review_context", "arguments": {"job_id": evidence["id"]}})
        require(wrong_job.get("isError"), "owner worker read the evidence worker's lease")
        inspected = inspector.tool("get_review_context", job_id=evidence["id"])
        require(inspected["evidence"] == events and "review_history" not in inspected, "unexpected leased evidence")
        routing = router.tool("get_review_context", job_id=owner["id"])
        require(routing["recommendations"][0]["owner"] == "records-team", "unexpected owner recommendation")
        require(routing["ranked_checks"][0]["step"] == "retention_days", "related control missing in worker context")
        for client, job, choice in ((inspector, evidence, "needs-evidence"), (router, owner, "needs-owner-review")):
            result = client.tool("finish_review", job_id=job["id"], disposition=choice)
            require(result["state"] == "complete" and result["disposition"] == choice, "worker receipt mismatch")

    before = http(port, reader, route)
    require(not before["review_history"], "worker wrote a human review")
    for _ in range(2):
        http(port, reviewer, route + "/reviews", payload=review)
    with mcp("local") as advisor:
        listed = advisor.tool("list_cases")
        require({r["id"] for r in listed} == {PRIMARY, FOLLOWUP, CONTROL}, "unexpected local MCP inventory")
        context = advisor.tool("get_case_context", case_id=PRIMARY)
        linked = advisor.tool("get_case_context", case_id=FOLLOWUP)
        unrelated = advisor.tool("get_case_context", case_id=CONTROL)
    require(len(context["review_history"]) == 1 and context["review_history"][0]["actor"] == "survey-reviewer"
            and context["review_history"][0]["accepted"] is True, "reviewer replay changed audit count")
    require(context["evidence"] == events and not unrelated["ranked_checks"], "unrelated evidence crossed revisions")
    dispositions = {}
    for case, actor, job, choice in ((context, "survey-evidence", evidence, "needs-evidence"),
                                     (linked, "survey-owner", owner, "needs-owner-review")):
        job_state = cli("handoff", "status", job["id"])
        require(job_state["state"] == "complete" and job_state["attempts"] == 1 and job_state["worker"] == actor,
                "durable queue receipt differs from lease")
        require(len(case["worker_outcomes"]) == 1, "expected one graph outcome")
        outcome = case["worker_outcomes"][0]
        require(outcome["job_id"] == job["id"] and outcome["worker"] == actor and outcome["disposition"] == choice,
                "graph outcome differs from queue receipt")
        dispositions[job["kind"]] = choice
    cli("identity", "revoke", "survey-reader", output=False)
    http(port, reader, route, expected=401)
    return {"dataset": "synthetic archive and index reviews", "cases": sorted(row["id"] for row in listed),
            "roles_exercised": ["operator", "reader", "reviewer", "local MCP reader", "evidence worker", "owner worker"],
            "manifest_sha256": digest, "ci_observations": expected, "completed_reviews": dispositions,
            "retrieved_prior_control": routing["ranked_checks"][0]["step"], "human_review_events": 1,
            "denials_verified": ["missing credential", "reader write", "reviewer claim", "worker workspace read",
                                 "other worker lease", "revoked reader"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="write receipt to a new file (default: stdout)")
    args = parser.parse_args()
    # Prevent proxy settings from routing loopback graph and readiness requests outside the host.
    with patch.dict(os.environ, no_proxy="*", NO_PROXY="*"), isolated_graph(), TemporaryDirectory() as path:
        folder = Path(path)
        key = folder / "ci.key"
        with os.fdopen(os.open(key, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as stream:
            stream.write(secrets.token_bytes(32))
        with patch.dict(os.environ, ICHNIX_IDENTITY_DB=str(folder / "ids.sqlite"), ICHNIX_CI_HMAC_KEY_FILE=str(key),
                        ICHNIX_AUTO_HANDOFFS="0"), ConsoleServer(("127.0.0.1", 0), Handler) as service, ExitStack() as stack:
            thread = Thread(target=service.serve_forever, daemon=True)
            thread.start()
            stack.callback(thread.join)
            stack.callback(service.shutdown)
            receipt = run(folder, service.server_port)
    encoded = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.out:
        with args.out.open("x") as file:
            file.write(encoded)
    else:
        sys.stdout.write(encoded)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"survey failed: {error}", file=sys.stderr)
        sys.exit(1)
