"""Check graph authentication, selected graph and trace rendering without writing data."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ichnix.graph import Graph
from ichnix.storage import checks as storage_checks


def main():
    if sys.argv[1:] in (["-h"], ["--help"]):
        print(__doc__)
        return 0
    if sys.argv[1:]:
        return 2
    try:
        checks = storage_checks("ready")
    except (OSError, ValueError):
        checks = {"sqlite_configuration": False}
    try:
        graph = Graph()
        graph._query("MATCH (r:Report) RETURN r.key AS key LIMIT 1")
        checks["hydradb"] = True
    except (OSError, ValueError, RuntimeError):
        checks["hydradb"] = False
    try:
        trace = subprocess.run(["node", str(ROOT / "ichnix" / "trace.mjs")], input=b"[]",
                               capture_output=True, timeout=10, check=True)
        checks["trace_renderer"] = b"<svg" in trace.stdout
    except (OSError, subprocess.SubprocessError):
        checks["trace_renderer"] = False
    print(json.dumps(checks))
    return int(not all(checks.values()))


if __name__ == "__main__":
    sys.exit(main())
