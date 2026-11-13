"""Pure opt-in native partial-start rollback regressions; no process control."""
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from dataclasses import replace

from scripts.ops_native_windows_witness import NativeBirth, NativeRestartBlocked
from scripts.ops_native_windows_rollback import (
    EnrolledChild, rollback_partial_start, deny_partial_generation_reuse,
)


class StubOriginalHandle:
    def __init__(self, pid, *, timeout=False, exit_code=0):
        self.pid = pid
        self.timeout = timeout
        self.exit_code = exit_code
        self.stopped = False

    def poll(self):
        return self.exit_code if self.stopped else None

    def wait(self, timeout):
        if self.timeout:
            raise subprocess.TimeoutExpired("owned-worker", timeout)
        self.stopped = True
        return self.exit_code


class PartialNativeRollbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.birth = NativeBirth(
            role="worker", generation=1, pid=212, created=1234.0,
            image="C:/python.exe", cwd="C:/owned", argv=("python", "-m", "worker"),
            username="limited", owner_marker="native-worker-test-owner-123456",
            config_sha256="a"*64,
        )
        self.handle = StubOriginalHandle(212)
        self.path = self.root / "worker-1.checkpoint.json"
        self.path.write_text(json.dumps({
            "schema":"ops-native-drain/1", "role":"worker", "generation":1,
            "owner_marker":self.birth.owner_marker,
            "config_sha256":"a"*64, "drained":True,
        }), encoding="utf-8")

    def rollback(self, **kwargs):
        return rollback_partial_start(
            root=self.root, generation=1,
            children=(EnrolledChild(self.birth, self.handle, self.path),),
            observe=lambda birth, handle: birth,
            **kwargs,
        )

    def test_clean_partial_rollback_seals_but_never_enables_restart(self):
        receipt = self.rollback()
        self.assertEqual(receipt["status"], "SEALED_PARTIAL")
        self.assertFalse(receipt["restart_permitted"])
        self.assertEqual(self.handle.poll(), 0)
        self.assertTrue((self.root/"native-0001-worker.stop.request").exists())
        with self.assertRaises(NativeRestartBlocked):
            deny_partial_generation_reuse(self.root, 1)

    def test_timeout_fails_closed_without_forced_termination(self):
        self.handle.timeout = True
        receipt = self.rollback(timeout=.01)
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertEqual(receipt["unverified_roles"], ["worker"])
        self.assertFalse(self.handle.stopped)

    def test_wrong_owner_refuses_before_stop_request(self):
        receipt = rollback_partial_start(
            root=self.root, generation=1,
            children=(EnrolledChild(self.birth, self.handle, self.path),),
            observe=lambda birth, handle: replace(birth, owner_marker="foreign"),
        )
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertFalse((self.root/"native-0001-worker.stop.request").exists())

    def test_corrupt_drain_receipt_refuses_clean_rollback(self):
        self.path.write_text('{"drained":true}')
        receipt = self.rollback()
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertEqual(receipt["unverified_roles"], ["worker"])

    def test_duplicate_rollback_receipt_cannot_be_overwritten(self):
        self.rollback()
        with self.assertRaises(NativeRestartBlocked):
            self.rollback()

    def test_unverified_second_child_blocks_both_signals(self):
        api = replace(self.birth, role="api", pid=213,
                      owner_marker="native-api-test-owner-123456")
        api_handle = StubOriginalHandle(213)
        api_path = self.root / "api-1.checkpoint.json"
        api_path.write_text("{}")
        receipt = rollback_partial_start(
            root=self.root, generation=1,
            children=(EnrolledChild(self.birth, self.handle, self.path),
                      EnrolledChild(api, api_handle, api_path)),
            observe=lambda birth, handle: replace(birth, image="foreign")
                    if birth.role == "api" else birth,
        )
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertFalse((self.root/"native-0001-worker.stop.request").exists())
        self.assertFalse((self.root/"native-0001-api.stop.request").exists())


if __name__ == "__main__":
    unittest.main()
