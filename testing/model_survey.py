"""Run two real Codex sessions on the bundled synthetic survey fixtures.

Requires an authenticated Codex CLI and the local HydraDB runtime. Each session sends
its assigned synthetic case to the configured model. The temporary workspace is
discarded and no model transcripts or credentials are printed.
"""

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Thread
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ichnix.credentials import issue
from ichnix.eval_graph import isolated_graph
from ichnix.handoffs import Handoffs
from ichnix.server import ConsoleServer, Handler
from ichnix.staging import checks, load


PERSONAS = [
    ("evidence-inspector", "SURVEY-101", "evidence-review",
     "Inspect the recorded staging observations. Report the exact failed control and the exact "
     "inconclusive control as fields failed_control and inconclusive_control. When an expected control "
     "has no conclusive observation, request additional evidence through the disposition."),
    ("owner-router", "SURVEY-102", "owner-review",
     "Read the current ownership recommendations and prior CI-check rankings. Return the top owner "
     "and top ranked check as owner and ranked_check. Record the appropriate disposition so that the "
     "human team can review the owner. Do not submit a human decision."),
]
MCP_TOOLS = ["claim_review", "get_review_context", "finish_review"]


def seed(graph):
    fixtures = ROOT / "testing" / "fixtures"
    cases = json.loads((fixtures / "cases.json").read_text())
    for report in cases:
        graph.import_report(report)
    report = graph.context("SURVEY-101")["report"]
    for observation in checks(report, load(fixtures / "policy.json"), load(fixtures / "manifest.json")):
        graph.add_evidence(report["id"], {**observation, "run_id": "model-survey"})


def expected_answer(graph, case_id, kind):
    context = graph.context(case_id)
    if kind == "evidence-review":
        failed, = [entry["step"] for entry in context["evidence"] if entry["result"] == "failed"]
        inconclusive, = [entry["step"] for entry in context["evidence"] if entry["result"] == "inconclusive"]
        return {"case_id": case_id, "failed_control": failed, "inconclusive_control": inconclusive,
                "disposition": "needs-evidence"}
    return {"case_id": case_id, "owner": context["recommendations"][0]["owner"],
            "ranked_check": context["ranked_checks"][0]["step"], "disposition": "needs-owner-review"}


def model_answer(root, profile, persona, instructions, fields):
    workdir = root / persona
    workdir.mkdir()
    schema = root / (persona + ".schema.json")
    schema.write_text(json.dumps({
        "type": "object", "properties": {name: {"type": "string"} for name in fields},
        "required": list(fields), "additionalProperties": False,
    }))
    command = ["codex", "exec", "--ephemeral", "--sandbox", "read-only", "--skip-git-repo-check",
               "--ignore-user-config", "-c", 'approval_policy="never"', "--json",
               "--output-schema", str(schema), "-C", str(workdir)]
    for key in ("command", "args"):
        command.extend(["-c", "mcp_servers.ichnix-worker." + key + "=" + json.dumps(profile[key])])
    for key, value in profile["env"].items():
        command.extend(["-c", "mcp_servers.ichnix-worker.env." + key + "=" + json.dumps(value)])
    command.append(
        f"You are the {persona} in a synthetic training workspace. {instructions} "
        "Use only the ichnix-worker MCP server. Claim exactly one job (lease_seconds 300), read the "
        "context for that job, then finish the job with your chosen disposition. Return its exact "
        "case ID and disposition in your answer. Use none of the shell, filesystem, web or other tools."
    )
    allowed = ("HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
               "XDG_RUNTIME_DIR", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy",
               "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    result = subprocess.run(command, cwd=workdir, env=env, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise RuntimeError(f"{persona}: Codex exited {result.returncode}; check normal login and model access")
    calls = []
    final = None
    ended = False
    for line in result.stdout.splitlines():
        event = json.loads(line)
        item = event.get("item", {})
        if event["type"] == "turn.completed":
            ended = True
        if event["type"] != "item.completed":
            continue
        if item.get("type") == "mcp_tool_call":
            if item.get("server") != "ichnix-worker" or item.get("status") != "completed":
                raise RuntimeError(f"{persona}: unexpected tool call or failed review")
            calls.append(item.get("tool"))
        elif item.get("type") == "agent_message":
            final = item["text"]
        elif item.get("type") != "reasoning":
            raise RuntimeError(f"{persona}: unexpected activity outside review tools")
    if not ended or calls != MCP_TOOLS or final is None:
        raise RuntimeError(f"{persona}: missing expected tool sequence or model answer")
    return json.loads(final)


def review(graph, workspace, root, registry, persona, case_id, kind, instructions):
    token = root / (persona + ".token")
    issue(registry, persona, "worker", token)
    job = Handoffs().enqueue(graph, case_id, kind)
    expected = expected_answer(graph, case_id, kind)
    config = subprocess.run([
        sys.executable, str(ROOT / "cli.py"), "mcp-config", "--profile", "worker",
        "--workspace-url", workspace, "--worker-token-file", str(token),
    ], check=True, text=True, capture_output=True)
    profile = json.loads(config.stdout)["mcpServers"]["ichnix-worker"]
    actual = model_answer(root, profile, persona, instructions, expected)
    if actual != expected:
        raise RuntimeError(f"{persona}: model answer differs from the fixture graph")
    receipt = Handoffs().status(job["id"])
    if (receipt["state"], receipt["worker"], receipt["attempts"]) != ("complete", persona, 1):
        raise RuntimeError(f"{persona}: invalid queue receipt")
    context = graph.context(case_id)
    outcome, = [o for o in context["worker_outcomes"] if o["job_id"] == job["id"]]
    if (outcome["worker"], outcome["kind"], outcome["disposition"]) != (persona, kind, actual["disposition"]):
        raise RuntimeError(f"{persona}: invalid graph outcome")
    if context["review_history"]:
        raise RuntimeError(f"{persona}: unexpected human review decision")
    return {"persona": persona, "model_answer": actual, "mcp_tools": MCP_TOOLS,
            "receipt_state": receipt["state"], "attempts": receipt["attempts"], "graph_outcome": outcome}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    results = []
    with tempfile.TemporaryDirectory() as tmp, isolated_graph() as graph:
        root = Path(tmp)
        registry = root / "identities.sqlite"
        seed(graph)
        with patch.dict(os.environ, ICHNIX_IDENTITY_DB=str(registry)), ConsoleServer(("127.0.0.1", 0), Handler) as server:
            with ExitStack() as cleanup:
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()
                cleanup.callback(thread.join)
                cleanup.callback(server.shutdown)
                for assignment in PERSONAS:
                    print("Starting synthetic " + assignment[0] + " session", file=sys.stderr, flush=True)
                    results.append(review(graph, f"http://127.0.0.1:{server.server_port}", root, registry, *assignment))
    print(json.dumps({"dataset": "bundled synthetic survey fixtures", "sessions": results}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
        print(str(error) if isinstance(error, RuntimeError) else f"model survey failed ({type(error).__name__})",
              file=sys.stderr)
        sys.exit(1)
