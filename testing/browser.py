"""Exercise public and role-scoped console sessions in disposable HydraDB workspaces."""
from argparse import ArgumentParser
from contextlib import contextmanager
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ichnix.credentials import issue
from ichnix.eval_graph import isolated_graph
from ichnix.server import ConsoleServer, Handler


@contextmanager
def sample_workspace():
    process = subprocess.Popen([sys.executable, "-u", str(ROOT / "demo.py"), "--public-readonly"],
                               stdout=subprocess.PIPE, text=True, cwd=ROOT)
    try:
        line = process.stdout.readline().strip()
        prefix = "Read-only synthetic demo on 127.0.0.1:"
        if not line.startswith(prefix):
            raise RuntimeError("synthetic demo failed to start")
        yield f"http://127.0.0.1:{int(line.removeprefix(prefix))}"
    finally:
        process.send_signal(signal.SIGINT)
        process.wait(timeout=15)


class HostedShell(Handler):
    """Mirror the two hosted route families using an isolated real console."""
    def dispatch(self, write):
        if self.path in ("/", "/console", "/workspace", "/workspace/") and not write:
            shell = "index.html" if self.path == "/" else "console.html"
            body = (ROOT / "dist" / shell).read_bytes()
            return self.send(200, body, "text/html; charset=utf-8")
        if self.path.startswith("/org-api/"):
            self.path = self.path.replace("/org-api/", "/api/", 1)
        elif self.path.startswith("/api/"):
            return self.send(503, {"error": "preview unavailable"})
        return super().dispatch(write)


@contextmanager
def named_workspace(hosted=False):
    if hosted:
        subprocess.run(["node", str(ROOT / "hosting/build-static.mjs")], cwd=ROOT, check=True)
    with TemporaryDirectory() as tmp, isolated_graph() as graph:
        registry = Path(tmp) / "identities.sqlite3"
        tokens = {}
        for role in ("read", "review", "worker", "intake"):
            out = Path(tmp) / f"{role}.token"
            issue(registry, f"browser-{role}", role, out)
            tokens[role] = out.read_text().strip()
        for report in json.loads((ROOT / "examples/cases.json").read_text()):
            graph.import_report(report)
        for index in range(19):
            graph.import_report({**report, "id": f"PAGE-{index:02d}"})
        with patch.dict(os.environ, ICHNIX_IDENTITY_DB=str(registry)), ConsoleServer(("127.0.0.1", 0), HostedShell if hosted else Handler) as http:
            thread = Thread(target=http.serve_forever, daemon=True)
            thread.start()
            try:
                yield f"http://127.0.0.1:{http.server_port}", tokens
            finally:
                http.shutdown()
                thread.join()


def public_checks(browser, origin, width):
    page = browser.new_page(viewport={"width": width, "height": 850}, reduced_motion="reduce")
    errors, external, exports = [], [], []
    page.on("request", lambda req: exports.append(req.url) if req.url.endswith("/selfgraph.json") else None)
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda req: external.append(urlsplit(req.url).netloc)
            if req.url.startswith(("http:", "https:")) and urlsplit(req.url).netloc != urlsplit(origin).netloc else None)
    try:
        for route in ("/", "/docs", "/console"):
            page.goto(origin + route)
            page.evaluate("document.fonts.ready")
            assert page.evaluate("document.documentElement.scrollWidth") == width
            if route == "/":
                page.locator("input[value=evidence]").focus()
                page.keyboard.press("ArrowRight")
                expect(page.locator(".revision-output")).to_be_visible()
            elif route == "/docs":
                index = page.locator(".index-panel")
                if width <= 720:
                    expect(index).not_to_have_attribute("open", "")
                    index.locator("summary").focus()
                    page.keyboard.press("Enter")
                index.get_by_role("link", name="Follow a finding").click()
                expect(page.locator("#walkthrough")).to_be_in_viewport()
                page.goto(origin + "/docs#worker-mcp")
                expect(page.locator("#worker-mcp")).to_have_attribute("open", "")
                summary = page.locator("#worker-mcp > summary")
                summary.focus()
                page.keyboard.press("Space")
                expect(page.locator("#worker-mcp")).not_to_have_attribute("open", "")
                page.keyboard.press("Enter")
                expect(page.locator("#worker-mcp pre").first).to_be_visible()
            else:
                expect(page.locator("#leave")).to_be_hidden()
                expect(page.locator("#report-upload")).to_be_hidden()
                expect(page.locator("#detail .review-row button")).to_have_count(0)
                def select(case):
                    if width <= 720:
                        page.locator("#case-select").select_option(case)
                    else:
                        page.locator(f'#cases button[data-case-id="{case}"]').click()
                    expect(page.locator(".case-id")).to_have_text(case)
                select("IX-108")
                download = page.get_by_role("link", name="Download SelfGraph facts (JSON)")
                expect(download).to_have_attribute("href", "/api/cases/IX-108/selfgraph.json")
                assert not exports
                with page.expect_download() as receipt:
                    download.focus()
                    page.keyboard.press("Enter")
                assert receipt.value.url == origin + "/api/cases/IX-108/selfgraph.json"
                assert receipt.value.suggested_filename == "IX-108-selfgraph.json"
                facts = json.loads(receipt.value.path().read_text())
                by_type = {entity["type"]: entity for entity in facts["entities"]}
                assert by_type["ReportSnapshot"]["name"] == "IX-108"
                assert by_type["ComponentRevision"]["properties"]["component"] == "audit-store"
                assert any(r["type"] == "HAS_OBSERVATION" for r in facts["relations"])
                assert page.request.get(origin + "/api/cases/PRIVATE/selfgraph.json").status == 404
                assert page.request.get(origin + "/api/case-page").status == 404
                expect(page.get_by_role("navigation", name="Case pages")).to_be_hidden()
                expect(page.locator(".trace")).to_be_visible()
                page.get_by_text("Review workflow diagram", exact=True).click()
                expect(page.locator(".review-diagram")).to_be_visible()
                page.locator(".review-diagram").evaluate("img => img.decode()")
                select("IX-113")
                expect(page.locator(".trace")).to_have_count(0)
                page.get_by_text("Review workflow diagram", exact=True).click()
                expect(page.locator(".review-diagram")).to_be_visible()
                if width > 720:
                    pending = []
                    def hold(route):
                        pending.append(route)
                    url = "**/api/cases/IX-108/diagram.svg"
                    page.route(url, hold)
                    select("IX-108")
                    with page.expect_request(lambda req: req.url.endswith("/IX-108/diagram.svg")):
                        page.get_by_text("Review workflow diagram", exact=True).click()
                    select("IX-113")
                    pending[0].fulfill(content_type="image/svg+xml",
                                       body='<svg xmlns="http://www.w3.org/2000/svg" width="23" height="23"/>')
                    page.unroute(url, hold)
                    expect(page.locator(".review-diagram")).to_have_count(0)
                # An older origin can return 404 for the new optional diagram.
                page.route("**/api/cases/IX-108/diagram.svg", lambda route: route.fulfill(status=404))
                select("IX-108")
                page.get_by_text("Review workflow diagram", exact=True).click()
                expect(page.get_by_text("This workspace could not supply", exact=False)).to_be_visible()
                expect(page.locator(".trace")).to_be_visible()
                page.get_by_text("Learning curve", exact=True).click()
                for caption, expected in (("Feedback from the same component revision", "100%"),
                                          ("Same feedback pooled across components", "33.3%")):
                    table = page.get_by_role("table", name=caption)
                    expect(table.locator("tbody tr")).to_have_count(4)
                    expect(table.locator("tbody tr").last.locator("td")).to_have_text([expected, expected])
        assert not errors, errors
        assert not external, external
    finally:
        page.close()


def role_checks(browser, origin, tokens, hosted=False):
    if hosted:
        preview = browser.new_page(viewport={"width": 320, "height": 700})
        try:
            preview.goto(origin)
            expect(preview.get_by_role("link", name="Private sign-in")).to_be_visible()
            assert preview.evaluate("document.documentElement.scrollWidth") == 320
            preview.goto(origin + "/console")
            expect(preview.locator("#status")).to_contain_text("could not complete")
            expect(preview.locator("#connect")).to_be_hidden()
            preview.get_by_role("link", name="Private sign-in").click()
            expect(preview.locator("#connect")).to_be_visible()
            expect(preview.get_by_role("heading", name="Organization workspace")).to_be_visible()
        finally:
            preview.close()
    for role, token in tokens.items():
        page = browser.new_page()
        leaked = []
        requests = []
        forbidden = []
        def observe(request):
            path = urlsplit(request.url).path
            if path.startswith("/api/" if hosted else "/org-api/"):
                forbidden.append(path)
            if token in request.url:
                leaked.append("credential in URL")
            if request.url.endswith("/diagram.svg"):
                requests.append(request.headers.get("authorization") == "Bearer " + token)
        page.on("request", observe)
        try:
            page.goto(origin + ("/workspace/" if hosted else "/console"))
            pending_cases = []
            if role == "intake":
                case_list = "**/" + ("org-api" if hosted else "api") + "/case-page"
                page.route(case_list, lambda route: pending_cases.append(route))
            page.get_by_label("Access token").fill(token)
            page.get_by_role("button", name="Connect", exact=True).click()
            if role == "intake":
                for _ in range(100):
                    if pending_cases:
                        break
                    page.wait_for_timeout(50)
                assert pending_cases, "initial case list was not requested"
                expect(page.locator("#report-upload")).to_be_hidden()
                pending_cases[0].continue_()
                page.unroute(case_list)
            if role == "worker":
                expect(page.locator("#status")).to_contain_text("denied")
                expect(page.locator("#report-upload")).to_be_hidden()
                expect(page.locator("#desk")).to_be_hidden()
                continue
            if role == "read":
                page.set_viewport_size({"width": 390 if hosted else 1440, "height": 850})
                pager = page.get_by_role("navigation", name="Case pages")
                expect(pager).to_be_visible()
                expect(pager.get_by_role("button", name="Previous")).to_be_disabled()
                if hosted:
                    # Supersede a delayed page load with a refresh of the visible page.
                    pending = []
                    cursor_request = "**/org-api/case-page?after=*"
                    page.route(cursor_request, lambda route: pending.append(route))
                    with page.expect_request(cursor_request):
                        pager.get_by_role("button", name="Next").click()
                    page.get_by_role("button", name="Refresh").click()
                    expect(page.locator("#status")).to_be_empty()
                    assert pending
                    with page.expect_response(cursor_request):
                        pending[0].continue_()
                    page.unroute(cursor_request)
                    expect(page.locator(".case-id")).to_have_text("IX-104")
                    expect(pager.get_by_role("button", name="Previous")).to_be_disabled()

                pager.get_by_role("button", name="Next").focus()
                page.keyboard.press("Enter")
                expect(page.locator(".case-id")).to_have_text("PAGE-17")
                expect(pager.get_by_role("button", name="Next")).to_be_disabled()
                expect(page.locator("#case-count")).to_have_text("2 on this page")
                pager.get_by_role("button", name="Previous").click()
                expect(page.locator(".case-id")).to_have_text("IX-104")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.set_viewport_size({"width": 1280, "height": 850})
            page.locator('#cases button[data-case-id="IX-108"]').click()
            expect(page.locator(".case-id")).to_have_text("IX-108")
            page.get_by_text("Review workflow diagram", exact=True).click()
            expect(page.locator(".review-diagram")).to_be_visible()
            assert requests == [True] and not leaked
            expect(page.get_by_role("link", name="Download SelfGraph facts (JSON)")).to_have_count(0)
            buttons = page.locator("#detail .review-row button")
            if role == "intake":
                expect(buttons).to_have_count(0)
                report = {"id": "ZZZ-BROWSER-INTAKE", "title": "Review uploaded policy",
                          "service": "ledger", "component": "audit", "revision": "r9", "owner": "platform"}
                page.locator("#report-file").set_input_files({"name": "mapped-case.json", "mimeType": "application/json",
                                                               "buffer": json.dumps(report).encode()})
                page.get_by_role("button", name="Import report").click()
                expect(page.locator(".case-id")).to_have_text("ZZZ-BROWSER-INTAKE")
                expect(page.locator("#detail h2")).to_have_text(report["title"])
            elif role == "read":
                expect(page.locator("#report-upload")).to_be_hidden()
                expect(buttons).to_have_count(0)
            else:
                expect(page.locator("#report-upload")).to_be_hidden()
                buttons.first.click()
                expect(page.locator("#status")).to_contain_text("accepted")
                history = page.locator(".history-panel summary")
                history.focus()
                page.keyboard.press("Enter")
                expect(page.locator(".review-history")).to_be_visible()
                expect(page.locator(".review-history")).to_contain_text("browser-review")
                page.keyboard.press("Space")
                expect(page.locator(".review-history")).to_be_hidden()
            page.get_by_role("button", name="Disconnect").click()
            expect(page.locator("#detail")).to_be_empty()
            expect(page.get_by_label("Access token")).to_have_value("")
        finally:
            assert not forbidden and not leaked
            page.close()


def main():
    args = ArgumentParser(description=__doc__)
    args.add_argument("--chrome", help="path to an installed Chrome executable")
    chrome = args.parse_args().chrome
    # Do not inherit a customer's registry, queue, provider settings, or relay credentials.
    clean = {k: v for k, v in os.environ.items() if not k.startswith(
        ("ICHNIX_", "HACKERONE_", "BUGCROWD_", "CLOUDFLARE_"))}
    with patch.dict(os.environ, clean, clear=True), sync_playwright() as playwright:
        with playwright.chromium.launch(executable_path=chrome) as browser:
            with sample_workspace() as origin:
                for width in (390, 1440):
                    public_checks(browser, origin, width)
            for hosted in (False, True):
                with named_workspace(hosted=hosted) as (origin, tokens):
                    role_checks(browser, origin, tokens, hosted=hosted)
    print("Chromium passed public and named role flows on native and hosted workspace routes")


if __name__ == "__main__":
    main()
