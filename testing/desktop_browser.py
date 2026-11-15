"""Exercise the shipped desktop frontend against a disposable loopback intake fixture."""

import argparse
from contextlib import contextmanager, nullcontext
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import platform
import secrets
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


PUBLIC = Path(__file__).resolve().parents[1] / "public"
REPORT = {
    "id": "DESKTOP-001", "title": "Receipt for the desktop café fixture",
    "service": "ledger", "revision": "demo-r1", "component": "audit", "owner": "records",
}


def encoded(report):
    return json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8-sig")


def loopback_origin(value):
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise argparse.ArgumentTypeError("invalid loopback port") from exc
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "::1")
            or parsed.username is not None or parsed.password is not None or not port
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise argparse.ArgumentTypeError("expected http://127.0.0.1:PORT or http://[::1]:PORT")
    return f"http://{parsed.netloc}"



@contextmanager
def workspace():
    token = secrets.token_urlsafe(32)
    payload = encoded(REPORT)
    uploads = []

    class Fixture(SimpleHTTPRequestHandler):
        extensions_map = {
            **SimpleHTTPRequestHandler.extensions_map,
            ".js": "text/javascript", ".css": "text/css", ".ttf": "font/ttf",
        }

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(PUBLIC), **kwargs)

        def log_message(self, *args):
            pass

        def reply(self, value, status=200):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            if self.headers.get("Authorization") == "Bearer " + token:
                return True
            self.reply({"error": "authentication required"}, 401)
            return False

        def do_GET(self):
            path = urlsplit(self.path).path
            if not path.startswith("/api/"):
                self.path = {"/docs": "/docs.html", "/console": "/console.html"}.get(path, self.path)
                return super().do_GET()
            if not self.authorized():
                return
            if path == "/api/session":
                return self.reply({"public_demo": False, "can_import": True, "can_review": False})
            if path == "/api/case-page":
                return self.reply({"cases": [REPORT] if uploads else [], "next": None, "previous": None})
            if path == "/api/cases/" + REPORT["id"] and uploads:
                return self.reply({"report": REPORT, "recommendations": [], "evidence": []})
            self.reply({"error": "unknown fixture route"}, 404)

        def do_POST(self):
            if self.path != "/api/import/report":
                return self.reply({"error": "unknown fixture route"}, 404)
            if not self.authorized():
                return
            if self.headers.get_content_type() != "application/json":
                return self.reply({"error": "expected JSON"}, 415)
            if self.headers.get("Content-Length") != str(len(payload)):
                return self.reply({"error": "unexpected fixture size"}, 400)
            raw = self.rfile.read(len(payload))
            if raw != payload:
                return self.reply({"error": "fixture bytes changed"}, 400)
            uploads.append(hashlib.sha256(raw).hexdigest())
            self.reply({"id": REPORT["id"]})

    with ThreadingHTTPServer(("127.0.0.1", 0), Fixture) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", token, uploads
        finally:
            server.shutdown()
            thread.join()


def check(browser, origin, token, report):
    headers = {"Authorization": "Bearer " + token}
    payload = encoded(report)
    imports = []
    with TemporaryDirectory() as directory:
        file = Path(directory) / "mapped-report.json"
        file.write_bytes(payload)
        page = browser.new_page(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: imports.append(request) if request.method == "POST"
                and request.url == origin + "/api/import/report" else None)
        # Keep the browser on the disposable origin even if the HTML later gains third-party links.
        page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/")
                   else route.abort())
        case_url = origin + "/api/cases/" + report["id"]
        try:
            session = page.request.get(origin + "/api/session", headers=headers, max_redirects=0)
            assert session.status == 200 and session.json().get("can_import") is True, "named intake token required"
            assert session.json().get("public_demo") is False, "expected a private test workspace"
            assert page.request.get(case_url, headers=headers, max_redirects=0).status == 404, "case already exists"
            page.goto(origin + "/docs")
            page.locator("#chapter-query").fill("self-host")
            install = page.locator('#chapter-links a[href="#install"]')
            expect(install).to_be_visible()
            install.click()
            expect(page.locator("#install h2").first).to_be_in_viewport()
            page.locator('.topnav a[href="/console"]').click()

            expect(page.locator("#report-upload")).to_be_hidden()
            page.get_by_label("Access token").fill(token)
            page.get_by_role("button", name="Connect", exact=True).click()
            drop = page.locator("#report-drop")
            expect(drop).to_be_visible()
            drop.drop({"files": file})
            expect(page.get_by_role("status")).to_have_text("Ready to import " + file.name)
            with page.expect_response(origin + "/api/import/report") as response:
                page.get_by_role("button", name="Import report").click()
            assert response.value.status == 200 and response.value.json() == {"id": report["id"]}
            expect(page.get_by_role("status")).to_have_text("Imported " + report["id"])
            expect(page.locator("#detail h2")).to_have_text(report["title"])
            stored = page.request.get(case_url, headers=headers, max_redirects=0)
            assert stored.status == 200 and stored.json()["report"] == report, "report readback differs"
            page.get_by_role("button", name="Disconnect").click()
            expect(page.get_by_label("Access token")).to_have_value("")
            expect(page.locator("#detail")).to_be_empty()
            assert len(imports) == 1 and imports[0].post_data_buffer == payload, "expected one unchanged file"
            assert not errors, errors
            return {"case_id": report["id"], "sha256": hashlib.sha256(payload).hexdigest(), "imports": len(imports)}
        finally:
            page.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", help="use a locally installed Chromium executable on Linux")
    parser.add_argument("--origin", type=loopback_origin,
                        help="upload one synthetic report to a disposable local console using ICHNIX_DESKTOP_TEST_TOKEN")
    parser.add_argument("--receipt", type=Path, help="write the synthetic UI receipt to this JSON file")
    args = parser.parse_args()
    if args.origin:
        token = os.environ.get("ICHNIX_DESKTOP_TEST_TOKEN")
        if not token:
            parser.error("real console checks require ICHNIX_DESKTOP_TEST_TOKEN")
        target = nullcontext((args.origin, token, None))
        report = {**REPORT, "id": "DESKTOP-" + secrets.token_hex(8)}
    else:
        target = workspace()
        report = REPORT
    with sync_playwright() as playwright:
        if args.executable and not sys.platform.startswith("linux"):
            parser.error("--executable is for the Linux Chromium check")
        if sys.platform == "win32":
            browser = playwright.chromium.launch(channel="msedge")
            engine = "Microsoft Edge"
        elif sys.platform == "darwin":
            browser = playwright.webkit.launch()
            engine = "Playwright WebKit"
        elif sys.platform.startswith("linux"):
            browser = playwright.chromium.launch(executable_path=args.executable)
            engine = "Chromium"
        else:
            parser.error("supported desktop platforms are Windows, macOS and Linux")
        with browser, target as (origin, token, uploads):
            result = {"platform": platform.system(), "browser": engine, "version": browser.version,
                      "api": "loopback console" if args.origin else "local fixture",
                      **check(browser, origin, token, report)}
            if uploads is not None:
                assert uploads == [result["sha256"]], "fixture did not receive the original file"
    receipt = json.dumps(result, indent=2)
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(receipt + "\n", encoding="utf-8")
    print(receipt)


if __name__ == "__main__":
    main()
