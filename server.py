"""Loopback web console. The reverse proxy owns TLS in a deployed installation."""
import argparse
import hmac
import json
import logging
import mimetypes
import os
import re
import subprocess
from threading import BoundedSemaphore
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from graph import Graph

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
MAX_BODY = 1024 * 1024


class ConsoleServer(ThreadingHTTPServer):
    # Apply backpressure before starting a worker, including idle connections.
    request_queue_size = 64

    def __init__(self, *args):
        self.slots = BoundedSemaphore(24)
        super().__init__(*args)

    def process_request(self, request, client_address):
        self.slots.acquire()
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Request targets can contain private report identifiers

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def send(self, status, body, kind="application/json"):
        if kind == "application/json":
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        size = int(self.headers.get("Content-Length", "-1"))
        if size < 0 or size > MAX_BODY:
            raise ValueError("body length")
        body = self.rfile.read(size)
        if len(body) != size:
            raise ValueError("incomplete request")
        return body

    def authorized(self, edit=False):
        supplied = self.headers.get("Authorization", "").encode()
        roles = ["ICHNIX_REVIEW_TOKEN"] if edit else ["ICHNIX_REVIEW_TOKEN", "ICHNIX_READ_TOKEN"]
        for role in roles:
            key = os.environ.get(role, "")
            if len(key) >= 32 and hmac.compare_digest(supplied, ("Bearer " + key).encode()):
                return True
        return False

    def dispatch(self, write):
        path = unquote(urlsplit(self.path).path)
        if path == "/hooks/hackerone" and write:
            from connectors import process_webhook
            secret = os.environ.get("ICHNIX_WEBHOOK_SECRET")
            if not secret:
                return self.send(404, {"error": "webhook disabled"})
            catalog = json.loads(Path(os.environ["ICHNIX_CATALOG"]).read_text())
            result = process_webhook(self.body(), self.headers, secret, os.environ["ICHNIX_PROGRAM"], catalog, Graph())
            return self.send(200, result)

        if not path.startswith("/api/"):
            if write:
                return self.send(405, {"error": "method"})
            route = {"/": "index.html", "/console": "console.html", "/console/": "console.html"}
            asset = (PUBLIC / route.get(path, path.lstrip("/"))).resolve()
            if not asset.is_relative_to(PUBLIC) or not asset.is_file() or any(part.startswith(".") for part in asset.relative_to(PUBLIC).parts):
                return self.send(404, {"error": "missing asset"})
            return self.send(200, asset.read_bytes(), mimetypes.guess_type(asset)[0] or "application/octet-stream")

        if not self.authorized(write):
            return self.send(401, {"error": "valid access token required"})
        graph = Graph()
        if path == "/api/cases" and not write:
            return self.send(200, graph.cases())
        match = re.fullmatch(r"/api/cases/([\w.:_-]{1,160})(?:/(reviews|trace\.svg))?", path)
        if not match:
            return self.send(404, {"error": "unknown route"})
        case_id, action = match.groups()
        if write and action == "reviews":
            data = json.loads(self.body())
            if not isinstance(data, dict) or type(data.get("accepted")) is not bool or not isinstance(data.get("owner"), str):
                raise ValueError("invalid review")
            context = graph.context(case_id)
            if data["owner"] not in [x["owner"] for x in context["recommendations"]]:
                raise ValueError("unknown recommendation")
            graph.record_review(case_id, data["owner"], data["accepted"])
            return self.send(200, graph.context(case_id))
        if not write and action != "reviews":
            data = graph.context(case_id)
            if action == "trace.svg":
                events = data["evidence"]
                latest = events[-1].get("run_id") if events else None
                events = [e for e in events if e.get("run_id") == latest]
                svg = subprocess.run(["node", str(ROOT / "trace.mjs")], input=json.dumps(events).encode(), capture_output=True, check=True, timeout=5).stdout
                return self.send(200, svg, "image/svg+xml")
            return self.send(200, data)
        return self.send(405, {"error": "method"})

    def handle_request(self, write):
        try:
            self.dispatch(write)
        except PermissionError:
            self.send(403, {"error": "forbidden"})
        except KeyError:
            self.send(404, {"error": "record unavailable"})
        except (ValueError, TypeError):
            self.send(400, {"error": "invalid input"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            logging.error("request failed (%s)", type(exc).__name__)
            self.send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "service unavailable"})

    def do_GET(self):
        self.handle_request(False)

    def do_POST(self):
        self.handle_request(True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args()
    if len(os.environ.get("ICHNIX_REVIEW_TOKEN", "")) < 32:
        parser.error("provide ICHNIX_REVIEW_TOKEN with at least 32 characters")
    ConsoleServer((args.bind, args.port), Handler).serve_forever()
