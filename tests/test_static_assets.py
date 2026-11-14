from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch
import unittest

from server import ConsoleServer, Handler


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
            with patch("server.PUBLIC", public), patch.object(handler, "send", side_effect=lambda *args: args):
                for route in ("/", "/index.html"):
                    handler.path = route
                    self.assertEqual(handler.dispatch(False), (200, b"landing", "text/html"))
                for route in ("/company-inventory.json", "/app.js", "/missing"):
                    handler.path = route
                    self.assertEqual(handler.dispatch(False)[0], 404)

    def test_video_handles_browser_seek_ranges(self):
        with TemporaryDirectory() as directory, patch("server.PUBLIC", Path(directory)):
            (Path(directory) / "ichnix-demo.mp4").write_bytes(b"abcdefgh")
            with ConsoleServer(("127.0.0.1", 0), Handler) as http:
                thread = Thread(target=http.serve_forever, daemon=True)
                thread.start()
                try:
                    for byte_range, status, content_range, body in (
                            (None, 200, None, b"abcdefgh"),
                            ("bytes=2-4", 206, "bytes 2-4/8", b"cde"),
                            ("bytes=5-", 206, "bytes 5-7/8", b"fgh"),
                            ("bytes=-2", 206, "bytes 6-7/8", b"gh"),
                            ("bytes=5-99", 206, "bytes 5-7/8", b"fgh"),
                            ("bytes=8-", 416, "bytes */8", b""),
                            ("bytes=6-2", 416, "bytes */8", b""),
                            ("bytes=-0", 416, "bytes */8", b""),
                            ("bytes=0-1,3-5", 200, None, b"abcdefgh")):
                        with self.subTest(byte_range=byte_range):
                            conn = HTTPConnection("127.0.0.1", http.server_port, timeout=3)
                            try:
                                conn.request("GET", "/ichnix-demo.mp4",
                                             headers={"Range": byte_range} if byte_range else {})
                                response = conn.getresponse()
                                self.assertEqual(response.status, status)
                                self.assertEqual(response.getheader("Accept-Ranges"), "bytes")
                                self.assertEqual(response.getheader("Content-Range"), content_range)
                                self.assertEqual(response.read(), body)
                            finally:
                                conn.close()
                finally:
                    http.shutdown()
                    thread.join()
