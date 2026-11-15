"""Run an isolated Compose workspace and upload from a browser on the desktop host."""
import argparse
import json
import os
from pathlib import Path
import socket
from socketserver import BaseRequestHandler, ThreadingTCPServer
from threading import Thread
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.bench_workers import Client


class WslConnection(BaseRequestHandler):
    """Bridge a native loopback socket into the disposable WSL distro without exposing a guest port."""

    def handle(self):
        with subprocess.Popen(["wsl.exe", "--user", "root", "--exec", "nc", "-N", "127.0.0.1", "8765"],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE) as peer:
            def send_request():
                try:
                    with peer.stdin:
                        while chunk := self.request.recv(65536):
                            peer.stdin.write(chunk)
                            peer.stdin.flush()
                except OSError:
                    pass  # A browser can cancel a request at either end.

            writer = Thread(target=send_request, daemon=True)
            writer.start()
            try:
                while chunk := peer.stdout.read1(65536):
                    self.request.sendall(chunk)
            except OSError:
                pass
            finally:
                try:
                    self.request.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                if peer.poll() is None:
                    peer.terminate()
                writer.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wsl", action="store_true", help="run Docker inside the default WSL 2 distribution")
    args = parser.parse_args()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = str(listener.getsockname()[1])
    project = "ichnix-desktop-test-" + uuid4().hex
    # Docker Desktop forwards ports itself. Bare WSL in CI needs a Windows loopback listener.
    guest_port = "8765" if args.wsl else port
    env = dict(os.environ, ICHNIX_PORT=guest_port)
    engine = ["wsl.exe", "--cd", str(ROOT), "--user", "root", "--exec", "env", f"ICHNIX_PORT={guest_port}"] if args.wsl else []
    command = [*engine, "docker", "compose", "-p", project, "--env-file", "examples/desktop.env"]

    def compose(*arguments, capture=False):
        return subprocess.run([*command, *arguments], cwd=ROOT, env=env, check=True,
                              stdout=subprocess.PIPE if capture else None, text=True)

    def credential(role):
        private_file = f"/var/lib/ichnix/desktop-{role}.token"
        compose("exec", "-T", "console", "python3", "cli.py", "identity", "issue",
                "desktop-" + role, "--role", role, "--out", private_file)
        return compose("exec", "-T", "console", "cat", private_file, capture=True).stdout.strip()

    forwarder = None
    try:
        compose("build")
        compose("run", "--rm", "console", "python3", "cli.py", "storage", "init")
        compose("up", "--detach", "--wait", "--wait-timeout", "180")
        if args.wsl:
            # Docker Desktop handles this bridge for end users. The Windows Server runner has only dockerd in WSL.
            forwarder = ThreadingTCPServer(("127.0.0.1", 0), WslConnection)
            forwarder.daemon_threads = True
            Thread(target=forwarder.serve_forever, daemon=True).start()
            port = str(forwarder.server_address[1])
        origin = "http://127.0.0.1:" + port
        intake = credential("intake")
        result = subprocess.run([sys.executable, "testing/desktop_browser.py", "--origin", origin],
                                cwd=ROOT, env=dict(env, ICHNIX_DESKTOP_TEST_TOKEN=intake), check=True,
                                stdout=subprocess.PIPE, text=True)
        receipt = json.loads(result.stdout)
        case_id = receipt["case_id"]
        print(f'{receipt["browser"]} on {receipt["platform"]} imported and read back the report', flush=True)
        client_env = dict(env, ICHNIX_WORKSPACE_URL=origin, ICHNIX_USER_TOKEN=intake)
        case = subprocess.check_output([sys.executable, "cli.py", "human", "--token-env", "ICHNIX_USER_TOKEN",
                                        "context", case_id], cwd=ROOT, env=client_env, text=True)
        assert json.loads(case)["report"]["id"] == case_id
        compose("exec", "-T", "console", "python3", "cli.py", "handoff", "enqueue", case_id, "owner-review")
        worker_env = dict(env, ICHNIX_WORKSPACE_URL=origin, ICHNIX_WORKER_TOKEN=credential("worker"))
        with subprocess.Popen([sys.executable, "cli.py", "mcp", "--worker", "--token-env", "ICHNIX_WORKER_TOKEN"],
                              cwd=ROOT, env=worker_env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as proc:
            try:
                client = Client(proc)
                client.initialize()
                job = client.tool("claim_review")
                assert job["case_id"] == case_id
                context = client.tool("get_review_context", job_id=job["id"])
                assert context["report"]["id"] == case_id
                assert client.tool("finish_review", job_id=job["id"])["state"] == "complete"
            finally:
                proc.stdin.close()
            assert proc.wait(timeout=10) == 0
        compose("exec", "-T", "console", "python3", "cli.py", "check-workspace")
    except subprocess.CalledProcessError:
        subprocess.run([*command, "ps", "--all"], cwd=ROOT, env=env)
        # These probes report only graph, store and console readiness booleans.
        subprocess.run([*command, "exec", "-T", "console", "python3", "scripts/check-workspace.py", "--console-ready"],
                       cwd=ROOT, env=env, timeout=45)
        raise
    finally:
        if forwarder is not None:
            forwarder.shutdown()
            forwarder.server_close()
        # Only remove this run's uniquely named graph and identity volumes.
        compose("down", "--volumes")


if __name__ == "__main__":
    main()
