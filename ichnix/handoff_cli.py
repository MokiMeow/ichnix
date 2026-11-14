"""Operator CLI for handing a case revision to one local reviewer at a time."""
import argparse
import json
import sqlite3
import sys

from ichnix.graph import Graph
from ichnix.handoffs import Handoffs, KINDS


def main():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="action", required=True)
    enqueue = commands.add_parser("enqueue")
    enqueue.add_argument("case_id")
    enqueue.add_argument("kind", choices=KINDS)
    claim = commands.add_parser("claim")
    claim.add_argument("worker")
    claim.add_argument("--lease-seconds", type=int, default=120)
    finish = commands.add_parser("finish", help="read the claim's token from stdin")
    finish.add_argument("job_id")
    finish.add_argument("worker")
    commands.add_parser("status").add_argument("job_id")
    args = root.parse_args()
    try:
        q = Handoffs()
        if args.action == "enqueue":
            result = q.enqueue(Graph(), args.case_id, args.kind)
        elif args.action == "claim":
            result = q.claim(args.worker, args.lease_seconds)
        elif args.action == "status":
            result = q.status(args.job_id)
        else:
            result = q.finish(args.job_id, args.worker, sys.stdin.readline(258).strip())
    except (OSError, sqlite3.Error, RuntimeError, ValueError, KeyError) as exc:
        print(f"handoff {args.action} failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
