"""Loopback web console. The reverse proxy owns TLS in a deployed installation."""
import argparse
import json
import hmac
import logging
import os
import re
import subprocess
from threading import BoundedSemaphore
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

from ichnix.credentials import identify, principal
from ichnix.graph import Graph
from ichnix.handoffs import Handoffs
from ichnix.intake import MAX_REPORT_BYTES, parse_mapped_report

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
PUBLIC_ASSETS = {
    "index.html", "console.html", "docs.html", "app.js", "manual.js", "styles.css", "docs.css",
    "strata.webp", "strata-mobile.webp", "nav.js", "404.html", "missing.css", "missing-trace.webp",
    "report-review.svg", "report-review.webp",
    "evidence-graph.png",
    "fonts/IBMPlexMono-Regular.woff2", "fonts/InstrumentSans.woff2",
    "fonts/IBMPlexMono-LICENSE.txt", "fonts/InstrumentSans-LICENSE.txt",
}

STATIC_TYPES = {
    ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".txt": "text/plain",
    ".svg": "image/svg+xml", ".png": "image/png", ".webp": "image/webp", ".woff2": "font/woff2",
}


class BodyTooLarge(ValueError):
    pass


def diagram_svg(data):
    return subprocess.run(["node", str(ROOT / "ichnix" / "trace.mjs")], input=json.dumps(data).encode(),
                          capture_output=True, check=True, timeout=5).stdout


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
    def version_string(self):
        return "Ichnix"

    def log_message(self, *args):
        pass  # Request targets can contain private report identifiers

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def send(self, status, body, kind="application/json", headers=(), *, cache="no-store"):
        if kind == "application/json":
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        if status != 304:
            self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", cache)
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; form-action 'self'; frame-src 'none'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        lengths = self.headers.get_all("Content-Length", [])
        if ("Transfer-Encoding" in self.headers or len(lengths) != 1
                or not re.fullmatch(r"[0-9]+", lengths[0], re.ASCII)):
            raise ValueError("expected one body length")
        size = int(lengths[0])
        if size > MAX_REPORT_BYTES:
            raise BodyTooLarge
        body = self.rfile.read(size)
        if len(body) != size:
            raise ValueError("incomplete request")
        return body

    def authorized(self, edit=False):
        self.review_actor = identify(self.headers.get("Authorization", ""), edit)
        return self.review_actor is not None

    def dispatch(self, write):
        path = unquote(urlsplit(self.path).path)
        if path.startswith("/api/handoffs/"):
            return self.handoff(path, write)
        if path == "/hooks/hackerone" and write:
            from ichnix.connectors import process_webhook
            secret = os.environ.get("ICHNIX_WEBHOOK_SECRET")
            if not secret:
                return self.send(404, {"error": "webhook disabled"})
            catalog = json.loads(Path(os.environ["ICHNIX_CATALOG"]).read_text())
            result = process_webhook(self.body(), self.headers, secret, os.environ["ICHNIX_PROGRAM"], catalog, Graph())
            return self.send(200, result)

        if path == "/hooks/bugcrowd" and write:
            from ichnix.connectors import process_bugcrowd_webhook
            secret = os.getenv("ICHNIX_BUGCROWD_WEBHOOK_SECRET")
            if not secret:
                return self.send(404, {"error": "webhook disabled"})
            catalog = json.loads(Path(os.environ["ICHNIX_BUGCROWD_CATALOG"]).read_text())
            result = process_bugcrowd_webhook(self.body(), self.headers, secret,
                                              os.environ["ICHNIX_BUGCROWD_ENGAGEMENT"], catalog, Graph())
            return self.send(200, result)

        if not path.startswith("/api/"):
            if write:
                return self.send(405, {"error": "method"})
            route = {"/": "index.html", "/console": "console.html", "/console/": "console.html",
                     "/docs": "docs.html", "/docs/": "docs.html"}
            name = route.get(path, path.removeprefix("/"))
            if name not in PUBLIC_ASSETS:
                if "text/html" in self.headers.get("Accept", ""):
                    page = PUBLIC / "404.html"
                    if page.resolve() == page and page.is_file():
                        return self.send(404, page.read_bytes(), "text/html; charset=utf-8")
                return self.send(404, {"error": "missing asset"})
            asset = PUBLIC / name
            if asset.resolve() != asset or not asset.is_file():
                return self.send(404, {"error": "missing asset"})
            kind = STATIC_TYPES[asset.suffix]
            if asset.suffix == ".html":
                return self.send(200, asset.read_bytes(), kind, cache="no-store")
            cache = ("public, max-age=86400" if asset.suffix in {".woff2", ".webp", ".png"}
                     else "public, max-age=0, must-revalidate")
            stat = asset.stat()
            tag = f'W/"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
            validators = [("ETag", tag)]
            if self.headers.get("If-None-Match") == tag:
                return self.send(304, b"", kind, validators, cache=cache)
            return self.send(200, asset.read_bytes(), kind, validators, cache=cache)

        if path == "/api/import/report" and write:
            if not identify(self.headers.get("Authorization", ""), intake=True):
                return self.send(403, {"error": "named intake credential required"})
            if self.headers.get_content_type() != "application/json" or "Content-Encoding" in self.headers:
                return self.send(415, {"error": "expected application/json"})
            report = parse_mapped_report(self.body())
            Graph().import_report(report)
            return self.send(200, {"id": report["id"]})

        if path == "/api/auth/status" and not write:
            identity = principal(self.headers.get("Authorization", ""))
            if identity is None:
                return self.send(401, {"error": "valid access token required"})
            return self.send(200, identity)
        if path == "/api/session" and not write:
            if not self.authorized():
                return self.send(401, {"error": "valid access token required"})
            return self.send(200, {"public_demo": False, "can_review": self.authorized(edit=True),
                                   "can_import": bool(identify(self.headers.get("Authorization", ""), intake=True))})
        if not self.authorized(write):
            return self.send(401, {"error": "valid access token required"})
        graph = Graph()
        if path == "/api/case-page" and not write:
            cursor = parse_qsl(urlsplit(self.path).query, keep_blank_values=True, strict_parsing=True, max_num_fields=1)
            return self.send(200, graph.case_page(*cursor[0]) if cursor else graph.case_page())
        if path == "/api/cases" and not write:
            return self.send(200, graph.cases())
        match = re.fullmatch(r"/api/cases/([\w.:_-]{1,160})(?:/(reviews|trace\.svg|diagram\.svg|readme\.md))?", path)
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
            graph.record_review(case_id, data["owner"], data["accepted"], actor=self.review_actor,
                                request_id=self.headers.get("Idempotency-Key"), expected_scope=data.get("scope"))
            return self.send(200, graph.context(case_id))
        if not write and action == "readme.md":
            return self.send(200, graph.case_readme(case_id).encode(), "text/markdown; charset=utf-8",
                             [("Content-Disposition", 'attachment; filename="README.md"')])
        if not write and action == "diagram.svg":
            plan, source = graph.review_diagram(case_id)
            return self.send(200, diagram_svg(plan), "image/svg+xml", [("X-Ichnix-Layout-Source", source)])
        if not write and action == "trace.svg":
            return self.send(200, diagram_svg(graph.evidence_trace(case_id)), "image/svg+xml")
        if not write and action != "reviews":
            return self.send(200, graph.context(case_id))
        return self.send(405, {"error": "method"})

    def handoff(self, path, write):
        if not write:
            return self.send(405, {"error": "POST required"})
        worker = identify(self.headers.get("Authorization", ""), worker=True)
        if worker is None:
            return self.send(403, {"error": "named worker credential required"})
        data = json.loads(self.body())
        if not isinstance(data, dict):
            raise ValueError("expected JSON object")
        if path == "/api/handoffs/claim" and set(data) <= {"lease_seconds"}:
            return self.send(200, Handoffs().claim(worker, data.get("lease_seconds", 120), graph_factory=Graph))
        job = re.fullmatch(r"/api/handoffs/([a-f0-9]{64})/(finish|context)", path)
        if job and job.group(2) == "context" and (set(data) == {"token"} or
                set(data) == {"token", "format"} and data["format"] == "markdown"):
            return self.send(200, Handoffs().case_context(Graph(), job.group(1), worker, data["token"],
                                                        metadata_only="format" in data))
        if job and job.group(2) == "finish" and set(data) in ({"token"}, {"token", "disposition"}):
            if "disposition" in data and not isinstance(data["disposition"], str):
                raise ValueError("expected a review disposition")
            return self.send(200, Handoffs().finish(job.group(1), worker, data["token"],
                                                    disposition=data.get("disposition"),
                                                    graph=Graph() if "disposition" in data else None))
        raise ValueError("invalid handoff request")

    def handle_request(self, write):
        try:
            if "ICHNIX_ORIGIN_SECRET" in os.environ:
                expected = os.environ["ICHNIX_ORIGIN_SECRET"]
                supplied = self.headers.get("X-Ichnix-Origin-Secret", "")
                if len(expected) < 32 or not hmac.compare_digest(supplied.encode(), expected.encode()):
                    raise PermissionError("origin access denied")
            self.dispatch(write)
        except PermissionError:
            self.send(403, {"error": "forbidden"})
        except KeyError:
            self.send(404, {"error": "record unavailable"})
        except BodyTooLarge:
            self.send(413, {"error": "request too large"})
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args()
    if "ICHNIX_IDENTITY_DB" not in os.environ and len(os.environ.get("ICHNIX_REVIEW_TOKEN", "")) < 32:
        parser.error("configure ICHNIX_IDENTITY_DB or provide a review token of at least 32 characters")
    ConsoleServer((args.bind, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
