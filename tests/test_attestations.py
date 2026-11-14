import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from eval_graph import isolated_graph
from staging import manifest_input, sign


class AttestationTests(unittest.TestCase):
    def test_exact_manifest_bytes_required_and_results_have_provenance(self):
        root = Path(__file__).resolve().parents[1]
        with isolated_graph() as graph, TemporaryDirectory() as temp:
            work = Path(temp)
            case = dict(id="ci-a", title="Retention review", service="storage", owner="ops",
                        component="archive", revision="v3")
            graph.import_report(case)
            key, manifest, signature, policy = [work / k for k in ("key", "manifest", "signature", "policy")]
            key.touch(mode=0o600)
            key.write_bytes(os.urandom(48))
            export = dict(service="storage", revision="v3", environment="staging", settings={"retention": 30})
            manifest.write_text(json.dumps(export))
            policy.write_text(json.dumps({"services": {"storage": {"retention": 30}}}))
            command = [sys.executable, str(root / "staging.py")]
            ingest = [*command, case["id"], "--manifest", str(manifest), "--policy", str(policy)]
            with patch.dict(os.environ, {"ICHNIX_CI_HMAC_KEY_FILE": str(key)}):
                subprocess.run([*command, "--sign", "--manifest", str(manifest), "--out", str(signature)], check=True)
                with self.assertRaises(FileExistsError):
                    sign(manifest, signature)
                self.assertEqual(subprocess.run(ingest, capture_output=True).returncode, 2)
                signed = [*ingest, "--signature", str(signature), "--run-id", "build-46"]
                events = json.loads(subprocess.check_output(signed))
                self.assertEqual(events[0]["source_method"], "hmac-sha256")
                self.assertEqual(events[0]["source_digest"], hashlib.sha256(manifest.read_bytes()).hexdigest())
                self.assertEqual(json.loads(subprocess.check_output(signed)), events)
                manifest.write_text(json.dumps(export) + " ")
                self.assertEqual(subprocess.run(signed, capture_output=True).returncode, 2)
                self.assertEqual(len(graph.context(case["id"])["evidence"]), 1)
                graph.add_evidence(case["id"], events[0])
                self.assertEqual(len(graph.context(case["id"])["evidence"]), 1)
            with patch.dict(os.environ):
                os.environ.pop("ICHNIX_CI_HMAC_KEY_FILE", None)
                _, unsigned = manifest_input(manifest)
                graph.add_evidence(case["id"], {**events[0], **unsigned})
                stored = graph.context(case["id"])["evidence"]
                self.assertEqual([row["source_method"] for row in stored], ["hmac-sha256", "local-file"])
                self.assertNotIn("settings", json.dumps(stored))
