"""Check graph authentication, selected graph and trace rendering without writing data."""
from http.client import HTTPConnection, HTTPException
import json
import os
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
    console_ready = sys.argv[1:] == ["--console-ready"]
    if sys.argv[1:] and not console_ready:
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
    if console_ready:
        # Only send the origin credential to the in-container loopback listener.
        conn = HTTPConnection("127.0.0.1", 8765, timeout=2)
        try:
            headers = {}
            if "ICHNIX_ORIGIN_SECRET" in os.environ:
                headers["X-Ichnix-Origin-Secret"] = os.environ["ICHNIX_ORIGIN_SECRET"]
            conn.request("GET", "/console", headers=headers)
            checks["console"] = conn.getresponse().status == 200
        except (OSError, HTTPException, ValueError):
            checks["console"] = False
        finally:
            conn.close()
        print(json.dumps(checks))
        return int(not all(checks.values()))
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
