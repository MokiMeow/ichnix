import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from credentials import issue, revoke
from demo import PublicDemoHandler
from eval_graph import isolated_graph
from handoffs import Handoffs
from server import ConsoleServer, Handler


class WorkerAPITests(unittest.TestCase):
    def test_named_roles_fenced_claims_and_readonly_demo(self):
        with isolated_graph() as graph, TemporaryDirectory() as temp:
            folder = Path(temp)
            db = folder / "identities.sqlite3"
            graph.import_report(dict(id="api-review", title="Retention review", component="audit",
                                     service="files", revision="v4", owner="records"))
            Handoffs().enqueue(graph, "api-review", "owner-review")
            tokens = {}
            for name, role in (("agent-a", "worker"), ("agent-b", "worker"), ("analyst", "review"), ("reader", "read")):
                issue(db, name, role, folder / name)
                tokens[name] = (folder / name).read_text().strip()
            with patch.dict(os.environ, {"ICHNIX_IDENTITY_DB": str(db)}), ConsoleServer(("127.0.0.1", 0), Handler) as server:
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()

                def call(path, actor, payload=None):
                    headers = {"Authorization": "Bearer " + tokens[actor], "Content-Type": "application/json"}
                    request = Request(f"http://127.0.0.1:{server.server_port}{path}",
                                      data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
                    try:
                        response = urlopen(request)
                    except HTTPError as error:
                        response = error
                    with response:
                        return response.status, json.load(response)

                claim = "/api/handoffs/claim"
                try:
                    for actor in ("reader", "analyst"):
                        self.assertEqual(call(claim, actor, {})[0], 403)
                    self.assertEqual(call(claim, "agent-a", {"worker": "agent-b"})[0], 400)
                    self.assertEqual(call("/api/cases/api-review", "agent-a")[0], 200)
                    self.assertEqual(call("/api/cases/api-review/reviews", "agent-a",
                                          {"owner": "records", "accepted": True})[0], 401)
                    status, job = call(claim, "agent-a", {"lease_seconds": 30})
                    self.assertEqual(status, 200)
                    self.assertEqual(job["worker"], "agent-a")
                    self.assertEqual(call(claim, "agent-b", {}), (200, None))
                    finish = "/api/handoffs/" + job["id"] + "/finish"
                    proof = {"token": job["token"]}
                    self.assertEqual(call(finish, "agent-b", proof)[0], 403)
                    self.assertEqual(call(finish, "agent-a", proof)[1]["state"], "complete")
                    self.assertEqual(call(finish, "agent-a", proof)[1]["state"], "complete")
                    revoke(db, "agent-a")
                    self.assertEqual(call(claim, "agent-a", {})[0], 403)
                finally:
                    server.shutdown()
                    thread.join()

    def test_public_demo_rejects_worker_routes(self):
        with ConsoleServer(("127.0.0.1", 0), PublicDemoHandler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for path in ("/api/handoffs/claim", "/api/handoffs/" + "1" * 64 + "/finish"):
                    with self.assertRaises(HTTPError) as error:
                        urlopen(Request(f"http://127.0.0.1:{server.server_port}{path}", data=b"{}"))
                    self.assertEqual(error.exception.code, 405)
                    error.exception.close()
            finally:
                server.shutdown()
                thread.join()
