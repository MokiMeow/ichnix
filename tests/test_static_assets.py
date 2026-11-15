from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from ichnix.server import Handler


class StaticAssetTests(unittest.TestCase):
    def test_only_named_regular_assets_are_public(self):
        with TemporaryDirectory() as private:
            root = Path(private)
            public = root / "public"
            public.mkdir()
            (public / "index.html").write_text("landing")
            (public / "strata.webp").write_bytes(b"art")
            (public / "company-inventory.json").write_text("local fixture")
            (public / "app.js").symlink_to(public / "company-inventory.json")
            handler = object.__new__(Handler)
            handler.headers = {}
            with patch("ichnix.server.PUBLIC", public), patch.object(handler, "send") as send:
                for route in ("/", "/index.html"):
                    handler.path = route
                    handler.dispatch(False)
                    send.assert_called_with(200, b"landing", "text/html", cache="no-store")
                handler.path = "/strata.webp"
                handler.dispatch(False)
                validators = send.call_args.args[3]
                send.assert_called_with(200, b"art", "image/webp", validators, cache="public, max-age=86400")
                handler.headers["If-None-Match"] = dict(validators)["ETag"]
                handler.dispatch(False)
                send.assert_called_with(304, b"", "image/webp", validators, cache="public, max-age=86400")
                for route in ("/company-inventory.json", "/app.js", "/missing"):
                    handler.path = route
                    handler.dispatch(False)
                    send.assert_called_with(404, {"error": "missing asset"})

                (public / "404.html").write_text("recovery links")
                handler.path = "/unknown/page"
                handler.headers["Accept"] = "text/html"
                handler.dispatch(False)
                send.assert_called_with(404, b"recovery links", "text/html; charset=utf-8")

    def test_guide_and_its_dependencies_can_be_reused_without_caching_the_console(self):
        with TemporaryDirectory() as directory:
            public = Path(directory)
            handler = object.__new__(Handler)
            handler.headers = {}
            with patch("ichnix.server.PUBLIC", public), patch.object(handler, "send") as send:
                for name in ("docs.html", "docs.css", "styles.css", "manual.js", "nav.js"):
                    (public / name).write_text("public guide")
                    handler.path = "/docs" if name == "docs.html" else "/" + name
                    handler.dispatch(False)
                    self.assertEqual(send.call_args.kwargs["cache"], "public, max-age=60")
                    tag = dict(send.call_args.args[3])["ETag"]
                    handler.headers = {"If-None-Match": tag}
                    handler.dispatch(False)
                    self.assertEqual(send.call_args.args[0], 304)
                    handler.headers = {}
                (public / "console.html").write_text("console")
                handler.path = "/console"
                handler.dispatch(False)
                self.assertEqual(send.call_args.kwargs["cache"], "no-store")
