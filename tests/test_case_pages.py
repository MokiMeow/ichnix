import json
import os
from contextlib import ExitStack
from http.client import HTTPConnection
from threading import Thread
from unittest import TestCase
from unittest.mock import patch

from ichnix.eval_graph import isolated_graph
from ichnix.server import ConsoleServer, Handler


class CasePageTests(TestCase):
    def test_live_pages_handle_insertions_and_keep_legacy_list(self):
        with isolated_graph() as graph, ExitStack() as stack:
            token = "test-reader-credential-for-case-pages"
            stack.enter_context(patch.dict(os.environ, ICHNIX_READ_TOKEN=token))
            server = stack.enter_context(ConsoleServer(("127.0.0.1", 0), Handler))
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            stack.callback(thread.join)
            stack.callback(server.shutdown)

            def get(query="", auth=True, path="/api/case-page"):
                conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                try:
                    conn.request("GET", path + query, headers={"Authorization": "Bearer " + token} if auth else {})
                    response = conn.getresponse()
                    return response.status, json.load(response)
                finally:
                    conn.close()

            self.assertEqual(get(auth=False)[0], 401)
            self.assertEqual(get(), (200, {"cases": [], "previous": None, "next": None}))
            report = dict(title="Service policy", owner="ops", service="inventory", revision="v1", component="catalog")
            for index in range(21):
                graph.import_report(dict(report, id=f"IX:{index:03d}"))
            _, first = get()
            self.assertEqual([r["id"] for r in first["cases"]], [f"IX:{i:03d}" for i in range(20)])
            self.assertIsNone(first["previous"])
            self.assertEqual(first["next"], "IX:019")
            # A concurrent insertion before the cursor cannot repeat the last case.
            graph.import_report(dict(report, id="AA-1"))
            _, second = get("?after=IX%3A019")
            self.assertEqual([r["id"] for r in second["cases"]], ["IX:020"])
            self.assertIsNone(second["next"])
            _, back = get("?before=" + second["previous"])
            self.assertEqual(first["cases"], back["cases"])
            self.assertEqual(back["previous"], "IX:000")
            self.assertEqual(get("?from=IX:020")[1], second)
            self.assertEqual(get("?after=ZZ")[1]["cases"], [])
            self.assertEqual(len(get(path="/api/cases")[1]), 22)
            for invalid in ("?after=", "?after=a&before=b", "?after=a&after=b", "?from=%2Fbad", "?other=1"):
                self.assertEqual(get(invalid)[0], 400)
