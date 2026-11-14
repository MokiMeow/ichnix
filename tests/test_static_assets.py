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
            (public / "company-inventory.json").write_text("local fixture")
            (public / "app.js").symlink_to(public / "company-inventory.json")
            handler = object.__new__(Handler)
            with patch("ichnix.server.PUBLIC", public), patch.object(handler, "send", side_effect=lambda *args: args):
                for route in ("/", "/index.html"):
                    handler.path = route
                    self.assertEqual(handler.dispatch(False), (200, b"landing", "text/html"))
                for route in ("/company-inventory.json", "/app.js", "/missing"):
                    handler.path = route
                    self.assertEqual(handler.dispatch(False)[0], 404)
