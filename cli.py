"""Ichnix operator CLI and MCP stdio entry point."""

import argparse
import json
import sys

import connectors
from mcp_server import serve


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
    load = commands.add_parser("import", help="import local metadata JSON as an object or array")
    load.add_argument("file")
    sync = commands.add_parser("sync", help="import reviewed HackerOne report metadata")
    sync.add_argument("--program", required=True)
    sync.add_argument("--catalog", required=True, help="JSON inventory keyed by report ID or asset")
    bc = commands.add_parser("sync-bugcrowd", help="import reviewed Bugcrowd submissions")
    bc.add_argument("--engagement", required=True, help="customer engagement code")
    bc.add_argument("--catalog", required=True, help="inventory JSON keyed by submission or target UUID")
    review = commands.add_parser("review", help="review a suggested remediation owner")
    review.add_argument("case_id")
    review.add_argument("owner")
    decisions = review.add_mutually_exclusive_group(required=True)
    decisions.add_argument("--accept", action="store_true")
    decisions.add_argument("--reject", action="store_true")
    commands.add_parser("mcp", help="start the read-only MCP server on stdio")
    return root


def run(args, graph):
    if args.command == "cases":
        return graph.cases()
    if args.command == "context":
        return graph.context(args.case_id)
    if args.command == "review":
        return graph.record_review(args.case_id, args.owner, args.accept)
    if args.command == "sync":
        return connectors.sync(args.program, _load(args.catalog), graph)
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
    args = parser().parse_args()
    from graph import Graph
    if args.command == "mcp":
        serve(Graph)
        return 0
    try:
        data = run(args, Graph())
        print(json.dumps(data, ensure_ascii=True, indent=2))
    except (OSError, KeyError, RuntimeError, ValueError) as error:
        # Upstream exception text may include private case data or credentials.
        print(f"ichnix: {args.command} failed ({type(error).__name__}). Check input and configuration.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
