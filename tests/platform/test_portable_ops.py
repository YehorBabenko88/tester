import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from scripts.portable_ops import (Ownership, OwnershipError, CheckpointError, write_checkpoint,
                                  load_checkpoint, resume_plan, request_cooperative_stop, resource_sample)

class PortableOpsTests(unittest.TestCase):
    def setUp(self):
        self.owner = Ownership("disposable-1", 123, "start-1", "/tmp/disposable")

    def test_checkpoint_recovery_reconciles_completed(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "checkpoint.json"
            write_checkpoint(p, self.owner, [
                {"id": "a", "state": "completed"}, {"id": "b", "state": "running"},
                {"id": "c", "state": "queued"}, {"id": "d", "state": "failed"}], 2)
            self.assertEqual(resume_plan(load_checkpoint(p, "disposable-1"), {"b"}), ["c"])

    def test_corruption_rejected(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "checkpoint.json"
            write_checkpoint(p, self.owner, [{"id": "a", "state": "queued"}], 0)
            envelope = json.loads(p.read_text())
            envelope["body"]["jobs"][0]["state"] = "completed"
            p.write_text(json.dumps(envelope))
            with self.assertRaises(CheckpointError):
                load_checkpoint(p, "disposable-1")

    def test_duplicate_job_rejected(self):
        with TemporaryDirectory() as td:
            with self.assertRaises(CheckpointError):
                write_checkpoint(Path(td) / "cp", self.owner,
                                 [{"id": "a", "state": "queued"}] * 2, 0)

    def test_wrong_instance_rejected(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "cp"
            write_checkpoint(p, self.owner, [], 0)
            with self.assertRaises(CheckpointError):
                load_checkpoint(p, "another")

    def test_ownership_refuses_signal(self):
        calls = []
        wrong = Ownership("disposable-1", 123, "different-start", "/tmp/disposable")
        with self.assertRaises(OwnershipError):
            request_cooperative_stop(self.owner, wrong, lambda: calls.append(1))
        self.assertEqual(calls, [])
        request_cooperative_stop(self.owner, self.owner, lambda: calls.append(1))
        self.assertEqual(calls, [1])

    def test_checkpoint_rejects_unexpected_job_payload(self):
        with TemporaryDirectory() as td:
            with self.assertRaises(CheckpointError):
                write_checkpoint(Path(td) / "cp", self.owner,
                                 [{"id": "a", "state": "queued", "command": "unexpected"}], 0)

    def test_malformed_checkpoint_fails_closed(self):
        with TemporaryDirectory() as td:
            cp = Path(td) / "cp"
            cp.write_text("{broken")
            with self.assertRaises(CheckpointError):
                load_checkpoint(cp, "disposable-1")

    def test_resource_sample(self):
        proc = SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=4096),
                               cpu_percent=lambda interval=None: 2.5, num_threads=lambda: 3)
        sample = resource_sample(123, lambda pid: proc)
        self.assertEqual((sample["rss_bytes"], sample["threads"]), (4096, 3))

if __name__ == "__main__":
    unittest.main()
