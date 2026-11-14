"""Measure real stdio MCP review clients against a disposable local deployment.

Run python3 scripts/bench_workers.py after installing the HydraDB runtime.
"""

import argparse
from contextlib import ExitStack
import json
import math
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
from threading import Thread
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def fixture(index):
    case_id = f"bench-{index}"
    report = {"id": case_id, "title": f"Retention evidence for ledger partition {index}",
              "component": f"retention-{index % 10}", "revision": "r1",
              "service": f"ledger-{index % 5}", "owner": "records"}
    evidence = {"step": "retention_days", "revision": "r1", "run_id": case_id,
                "result": "passed" if index % 2 == 0 else "failed"}
    return report, evidence


def write_frame(stream, frame):
    stream.write(json.dumps(frame) + "\n")
    stream.flush()


class Client:
    """A benchmark participant speaking MCP to its own CLI subprocess."""

    def __init__(self, process):
        self.process = process
        self.serial = 0

    def request(self, method, params=None):
        self.serial += 1
        write_frame(self.process.stdin, {"jsonrpc": "2.0", "id": self.serial,
                                       "method": method, "params": params or {}})
        reply = json.loads(self.process.stdout.readline())
        if "result" not in reply or reply.get("id") != self.serial or reply.get("jsonrpc") != "2.0":
            raise RuntimeError(f"{method} protocol error")
        return reply["result"]

    def tool(self, name, **arguments):
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise RuntimeError(f"{name} failed")
        return json.loads(result["content"][0]["text"])

    def initialize(self):
        from ichnix.mcp_server import PROTOCOL
        result = self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                             "clientInfo": {"name": "ichnix-benchmark", "version": "1"}})
        if result["protocolVersion"] != PROTOCOL:
            raise RuntimeError("protocol negotiation failed")
        write_frame(self.process.stdin, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        expected = {"claim_review", "get_review_context", "finish_review"}
        if expected != {tool["name"] for tool in self.request("tools/list")["tools"]}:
            raise RuntimeError("worker tools missing")

    def review(self):
        started = time.perf_counter()
        job = self.tool("claim_review")
        if job is None or job["kind"] != "evidence-review":
            raise RuntimeError("missing evidence job")
        context = self.tool("get_review_context", job_id=job["id"])
        report, evidence = fixture(int(job["case_id"].removeprefix("bench-")))
        rows = [{key: item[key] for key in evidence} for item in context["evidence"]]
        if context["report"] != report or rows != [evidence]:
            raise RuntimeError("retrieved case or CI evidence mismatch")
        done = self.tool("finish_review", job_id=job["id"])
        if done["id"] != job["id"] or done["state"] != "complete":
            raise RuntimeError("invalid completion receipt")
        return job["id"], round((time.perf_counter() - started) * 1000, 3)


def participant(quota):
    with subprocess.Popen([sys.executable, str(ROOT / "cli.py"), "mcp", "--worker"],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as proc:
        client = Client(proc)
        client.initialize()
        write_frame(sys.stdout, {"ready": True})
        if sys.stdin.readline() != "go\n":
            return 1
        receipts, errors = [], []
        try:
            for _ in range(quota):
                receipts.append(client.review())
        except (ValueError, RuntimeError, OSError, KeyError, IndexError) as exc:
            errors.append(str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__)
        write_frame(sys.stdout, {"receipts": receipts, "errors": errors})
    return 0


def read_frame(proc, deadline):
    if not select.select([proc.stdout], [], [], max(0, deadline - time.monotonic()))[0]:
        raise RuntimeError("benchmark client timed out")
    raw = proc.stdout.readline()
    if not raw:
        raise RuntimeError("benchmark client exited before sending its result")
    return json.loads(raw)


def close_client(proc):
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    proc.wait()
    proc.stdin.close()
    proc.stdout.close()


def run_batch(graph, root, reports, workers):
    from ichnix.credentials import issue
    from ichnix.handoffs import Handoffs
    from ichnix.server import ConsoleServer, Handler

    directory = root / str(workers)
    directory.mkdir(mode=0o700)
    identities = directory / "workers.sqlite"
    with patch.dict(os.environ, ICHNIX_STATE_DIR=str(directory), ICHNIX_IDENTITY_DB=str(identities)):
        queue = Handoffs()
        expected = {queue.enqueue(graph, r["id"], "evidence-review")["id"] for r in reports}
        for i in range(workers):
            issue(identities, f"worker-{i}", "worker", directory / f"worker-{i}.token")
        with ConsoleServer(("127.0.0.1", 0), Handler) as server, ExitStack() as cleanup:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            cleanup.callback(thread.join)
            cleanup.callback(server.shutdown)
            boot = time.perf_counter()
            clients = []
            for i in range(workers):
                quota = len(reports) // workers + int(i < len(reports) % workers)
                env = {"PATH": os.environ["PATH"], "ICHNIX_WORKSPACE_URL": f"http://127.0.0.1:{server.server_port}",
                       "ICHNIX_WORKER_TOKEN_FILE": str(directory / f"worker-{i}.token")}
                proc = subprocess.Popen([sys.executable, __file__, "--participant", str(quota)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                        start_new_session=True, env=env)
                cleanup.callback(close_client, proc)
                clients.append(proc)
            deadline = time.monotonic() + 180
            for proc in clients:
                if read_frame(proc, deadline) != {"ready": True}:
                    raise RuntimeError("client initialization failed")
            start = time.perf_counter()
            for proc in clients:
                proc.stdin.write("go\n")
                proc.stdin.flush()
            deadline = time.monotonic() + 300
            results = [read_frame(proc, deadline) for proc in clients]
            elapsed = time.perf_counter() - start

        errors = [e for result in results for e in result["errors"]]
        owners = {}
        times = []
        for i, result in enumerate(results):
            for job_id, duration in result["receipts"]:
                if job_id in owners:
                    errors.append("duplicate receipt")
                owners[job_id] = f"worker-{i}"
                times.append(duration)
        if set(owners) != expected:
            errors.append("receipt set differs from queued jobs")
        for job_id, worker in owners.items():
            persisted = queue.status(job_id)
            if persisted["state"] != "complete" or persisted["worker"] != worker or persisted["attempts"] != 1:
                errors.append("persisted completion mismatch")
        times.sort()
        return {"workers": workers, "jobs_enqueued": len(expected), "jobs_verified": len(owners),
                "worker_identities_with_receipts": len(set(owners.values())), "errors": errors,
                "startup_seconds": round(start - boot, 3), "review_seconds": round(elapsed, 3),
                "verified_jobs_per_second": round(len(owners) / elapsed, 2),
                "p50_job_ms": times[math.ceil(len(times) * 0.5) - 1] if times else None,
                "p95_job_ms": times[math.ceil(len(times) * 0.95) - 1] if times else None}


def main():
    from ichnix.eval_graph import isolated_graph
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=100, help="compare this concurrency to one worker")
    parser.add_argument("--jobs", type=int, default=100, help="identical job count per run")
    args = parser.parse_args()
    if not 2 <= args.workers <= 100 or not args.workers <= args.jobs <= 1000:
        parser.error("require 2 <= workers <= 100 and workers <= jobs <= 1000")
    with isolated_graph() as graph, tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        reports, evidence = zip(*(fixture(i) for i in range(args.jobs)))
        inputs = root / "reports.json"
        inputs.write_text(json.dumps(reports))
        subprocess.run([sys.executable, str(ROOT / "cli.py"), "import", str(inputs)],
                       check=True, stdout=subprocess.DEVNULL)
        for report, obs in zip(reports, evidence):
            graph.add_evidence(report["id"], obs)
        runs = [run_batch(graph, root, reports, workers) for workers in (1, args.workers)]
    print(json.dumps({"dataset": "synthetic retention reviews on loopback", "runs": runs}, indent=2))
    return int(any(run["errors"] for run in runs))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--participant":
        sys.exit(participant(int(sys.argv[2])))
    sys.exit(main())
