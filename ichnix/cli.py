"""Ichnix operator CLI and MCP stdio entry point."""

import argparse
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

from ichnix import connectors
from ichnix.mcp_server import serve


ROOT = Path(__file__).resolve().parents[1]
OPERATOR_COMMANDS = {
    "storage": ("ichnix.storage", "initialize or check the private SQLite stores"),
    "survey": ("testing/survey.py", "exercise synthetic human and agent workflows in a disposable workspace"),
    "identity": ("ichnix.credentials", "issue, list and revoke named workspace identities"),
    "handoff": ("ichnix.handoff_cli", "queue cases, claim reviews and check lease status"),
    "staging": ("ichnix.staging", "verify or sign the staging configuration manifest"),
    "acceptance": ("scripts/acceptance.py", "exercise the end-to-end workspace acceptance checks"),
    "check-workspace": ("scripts/check-workspace.py", "check the configured workspace and local dependencies"),
    "bench-workers": ("scripts/bench_workers.py", "measure concurrent remote review workers on an isolated graph"),
    "evaluate": ("ichnix.eval_graph", "measure retrieval and load on an isolated graph"),
    "console": ("ichnix.server", "serve the persistent workspace using configured credentials"),
    "demo": ("ichnix.demo", "start a disposable preview with sample cases or a live public disclosure"),
}


def _load(path):
    with open(path, "rb") as handle:
        raw = handle.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("input file exceeds size limit")
    return json.loads(raw)


def parser():
    root = argparse.ArgumentParser(prog="ichnix", description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("cases", help="list case metadata")
    context = commands.add_parser("context", help="get graph context for one case")
    context.add_argument("case_id")
    export = commands.add_parser("export-selfgraph", help="export the current snapshot and its observed CI facts for SelfGraph")
    export.add_argument("case_id")
    export.add_argument("--out", required=True, type=Path, help="create a new private JSON file")
    load = commands.add_parser("import", help="import local metadata JSON as an object or array")
    load.add_argument("file")
    sync = commands.add_parser("sync", help="import reviewed HackerOne report metadata")
    sync.add_argument("--program", required=True)
    sync.add_argument("--catalog", required=True, help="JSON inventory keyed by report ID or asset")
    disclosed = commands.add_parser("import-disclosed", help="import disclosed public HackerOne report metadata")
    disclosed.add_argument("--program", required=True)
    disclosed.add_argument("--report-id", required=True)
    disclosed.add_argument("--catalog", required=True, help="JSON inventory keyed by report ID or asset")
    bc = commands.add_parser("sync-bugcrowd", help="import reviewed Bugcrowd submissions")
    bc.add_argument("--engagement", required=True, help="customer engagement code")
    bc.add_argument("--catalog", required=True, help="inventory JSON keyed by submission or target UUID")
    human = commands.add_parser("human", help="read or review through the private HTTP console with a named credential")
    human.add_argument("--workspace-url", help="private workspace origin (ICHNIX_WORKSPACE_URL)")
    human.add_argument("--token-file", help="reader or reviewer credential (ICHNIX_USER_TOKEN_FILE)")
    remote = human.add_subparsers(dest="action", required=True)
    remote.add_parser("cases", help="list the reports available to this identity")
    remote.add_parser("context", help="read a case and its evidence").add_argument("case_id")
    remote_review = remote.add_parser("review", help="record a named owner decision")
    remote_review.add_argument("--request-id", help="stable idempotency key when retrying a decision")
    review = commands.add_parser("review", help="review a suggested remediation owner")
    for entry in (review, remote_review):
        entry.add_argument("case_id")
        entry.add_argument("owner")
        decisions = entry.add_mutually_exclusive_group(required=True)
        decisions.add_argument("--accept", action="store_true")
        decisions.add_argument("--reject", action="store_true")
    mcp = commands.add_parser("mcp", help="serve local read-only or remote worker tools over stdio")
    mcp.add_argument("--worker", action="store_true", help="claim and complete leased reviews through a private workspace")
    mcp.add_argument("--workspace-url", help="workspace origin (ICHNIX_WORKSPACE_URL)")
    mcp.add_argument("--worker-token-file", help="private credential file (ICHNIX_WORKER_TOKEN_FILE)")
    config = commands.add_parser("mcp-config", help="generate a stdio client configuration for this installation")
    config.add_argument("--profile", required=True, choices=["worker", "local"])
    config.add_argument("--format", choices=["json", "codex"], default="json",
                        help="mcpServers JSON or Codex TOML configuration")
    config.add_argument("--workspace-url", help="private workspace origin for the worker profile")
    config.add_argument("--worker-token-file", help="named worker credential file")
    config.add_argument("--hydra-url", help="HydraDB origin for the local profile")
    config.add_argument("--hydra-token-file", help="HydraDB credential file for the local profile")
    config.add_argument("--out", type=Path, help="write a new configuration file instead of standard output")
    for name, (_, help_text) in OPERATOR_COMMANDS.items():
        commands.add_parser(name, help=help_text)
    return root


def mcp_config(args):
    from ichnix.worker_client import Worker, workspace_origin
    from ichnix.storage import state_directory
    command = [str(ROOT / "cli.py"), "mcp"]
    if args.profile == "worker":
        if args.hydra_url or args.hydra_token_file:
            raise ValueError("worker profiles take workspace options")
        origin = args.workspace_url or os.getenv("ICHNIX_WORKSPACE_URL")
        source = args.worker_token_file or os.getenv("ICHNIX_WORKER_TOKEN_FILE")
        if not source:
            raise ValueError("worker credential file required")
        token_file = Path(source).expanduser().resolve(strict=True)
        Worker(origin, token_file)
        env = {"ICHNIX_WORKSPACE_URL": origin, "ICHNIX_WORKER_TOKEN_FILE": str(token_file)}
        command.append("--worker")
    else:
        if args.workspace_url or args.worker_token_file:
            raise ValueError("local profiles take HydraDB options")
        source = args.hydra_token_file or os.getenv("ICHNIX_HYDRA_TOKEN_FILE") or state_directory() / "hydra-token"
        token_file = Path(source).expanduser().resolve(strict=True)
        if not token_file.is_file() or token_file.stat().st_mode & 0o077 or not 0 < token_file.stat().st_size <= 4096:
            raise ValueError("private HydraDB credential file required")
        origin = args.hydra_url or os.getenv("ICHNIX_HYDRA_URL", "http://127.0.0.1:8443")
        parsed = workspace_origin(origin)
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("unsupported local HydraDB origin")
        env = {"ICHNIX_HYDRA_URL": origin, "ICHNIX_HYDRA_TOKEN_FILE": str(token_file), "ICHNIX_HYDRA_TOKEN": ""}
        for key, default in (("NAMESPACE", "default"), ("GRAPH", "default"), ("CELL", "cell-0")):
            name = "ICHNIX_HYDRA_" + key
            env[name] = os.getenv(name, default)
    entry = {"command": sys.executable, "args": command, "env": env}
    name = "ichnix-" + args.profile
    if args.format == "codex":
        lines = [f"[mcp_servers.{name}]", "command = " + json.dumps(entry["command"], ensure_ascii=False),
                 "args = " + json.dumps(command, ensure_ascii=False), "", f"[mcp_servers.{name}.env]"]
        lines.extend(key + " = " + json.dumps(value, ensure_ascii=False) for key, value in env.items())
        rendered = "\n".join(lines)
    else:
        rendered = json.dumps({"mcpServers": {name: entry}}, indent=2)
    if args.out is None:
        print(rendered)
    else:
        with os.fdopen(os.open(args.out, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600), "w") as output:
            print(rendered, file=output)
        print("wrote stdio client configuration")


def human_request(args):
    from uuid import uuid4
    from ichnix.graph import _case_id
    from ichnix.worker_client import WorkspaceClient
    client = WorkspaceClient(args.workspace_url or os.getenv("ICHNIX_WORKSPACE_URL"),
                             args.token_file or os.getenv("ICHNIX_USER_TOKEN_FILE"))
    route = "/api/cases"
    if args.action == "cases":
        return client.request("GET", route)
    route += "/" + _case_id(args.case_id)
    context = client.request("GET", route)
    if args.action == "context":
        return context
    report = context["report"]
    body = {"owner": args.owner, "accepted": args.accept,
            "scope": {key: report[key] for key in ("service", "component", "revision")}}
    return client.request("POST", route + "/reviews", body, request_id=args.request_id or str(uuid4()))


def run(args, graph):
    if args.command == "cases":
        return graph.cases()
    if args.command == "context":
        return graph.context(args.case_id)
    if args.command == "export-selfgraph":
        facts = graph.selfgraph_facts(args.case_id)
        # Publish a complete 0600 file without overwriting an existing export or following its symlink.
        with NamedTemporaryFile(mode="w", dir=args.out.parent, encoding="utf-8") as tmp:
            json.dump(facts, tmp, ensure_ascii=True)
            tmp.write("\n")
            tmp.flush()
            os.fsync(tmp.fileno())
            os.link(tmp.name, args.out)
        return {"format": "selfgraph-facts", "entities": len(facts["entities"]), "relations": len(facts["relations"])}
    if args.command == "review":
        return graph.record_review(args.case_id, args.owner, args.accept)
    if args.command == "sync":
        return connectors.sync(args.program, _load(args.catalog), graph)
    if args.command == "import-disclosed":
        return connectors.import_disclosed(args.program, args.report_id, _load(args.catalog), graph)
    if args.command == "sync-bugcrowd":
        return connectors.sync_bugcrowd(args.engagement, _load(args.catalog), graph)
    records = _load(args.file)
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
        raise ValueError("expected an object or array of report metadata")
    for row in records:
        graph.import_report(row)
    return {"imported": len(records)}


def main():
    if len(sys.argv) > 1 and sys.argv[1] in OPERATOR_COMMANDS:
        target, _ = OPERATOR_COMMANDS[sys.argv[1]]
        if target.startswith("ichnix."):
            from importlib import import_module
            sys.argv = [target, *sys.argv[2:]]
            return import_module(target).main()
        os.execv(sys.executable, [sys.executable, str(ROOT / target), *sys.argv[2:]])
    args = parser().parse_args()
    try:
        if args.command == "human":
            print(json.dumps(human_request(args), ensure_ascii=True, indent=2))
            return 0
        if args.command == "mcp-config":
            mcp_config(args)
            return 0
        if args.command == "mcp" and args.worker:
            from ichnix.worker_client import Worker
            worker = Worker(args.workspace_url or os.getenv("ICHNIX_WORKSPACE_URL"),
                            args.worker_token_file or os.getenv("ICHNIX_WORKER_TOKEN_FILE"))
            serve(worker=worker)
            return 0
        from ichnix.graph import Graph
        if args.command == "mcp":
            if args.workspace_url or args.worker_token_file:
                raise ValueError("workspace options require --worker")
            serve(Graph)
            return 0
        data = run(args, Graph())
        print(json.dumps(data, ensure_ascii=True, indent=2))
    except (OSError, KeyError, RuntimeError, ValueError) as error:
        # Upstream exception text may include private case data or credentials.
        print(f"ichnix: {args.command} failed ({type(error).__name__}). Check input and configuration.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
