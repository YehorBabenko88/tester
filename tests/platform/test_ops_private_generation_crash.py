"""Isolated crash and same-UID writer tests; never accesses PC4 or SCM."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import ops_private_generation_prototype as mod


class CrashAndConcurrencyTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.sandbox = Path(temp.name)
        self.root = self.sandbox / "private"
        self.root.mkdir(mode=0o700)
        self.root.chmod(0o700)
        self.live = self.sandbox / "active"
        self.live.mkdir()
        (self.live / "worker.py").write_bytes(b"PROTECTED")
        self.pins = {"OPS": "a" * 40}
        self.blobs = [self.blob("src/api/main.py", b"REVIEWED"),
                      self.blob("tests/platform/test_fixture.py", b"TEST")]
        self.manifest = mod.canonical({
            "schema": mod.SCHEMA, "source_pins": self.pins,
            "files": [dict(path=b.path, sha256=b.sha256,
                           git_blob_sha1=b.git_blob_sha1, mode=b.mode)
                      for b in self.blobs],
        })
        self.pin = mod.hash256(self.manifest)

    @staticmethod
    def blob(path, raw):
        return mod.ReviewedBlob(path, raw, mod.hash256(raw), mod.git_hash(raw))

    def publish(self, **opts):
        return mod.publish_generation(
            self.root, blobs=self.blobs, manifest=self.manifest,
            manifest_sha256=self.pin, source_pins=self.pins, **opts)

    def current(self):
        return mod.read_current(
            self.root, manifest=self.manifest, manifest_sha256=self.pin)

    def pinned(self, generation):
        return mod.read_pinned(
            self.root, generation, manifest=self.manifest,
            manifest_sha256=self.pin)

    def test_crash_before_current_switch_keeps_old(self):
        old = self.publish()
        replace = os.replace

        def crash(src, dst):
            if Path(dst).name == "CURRENT":
                raise OSError("BEFORE_SWITCH")
            return replace(src, dst)

        with patch.object(mod.os, "replace", side_effect=crash):
            with self.assertRaisesRegex(OSError, "BEFORE_SWITCH"):
                self.publish()
        self.assertEqual(self.current(), old)

    def test_crash_after_current_switch_exposes_complete_new(self):
        old = self.publish()
        replace = os.replace

        def crash(src, dst):
            result = replace(src, dst)
            if Path(dst).name == "CURRENT":
                raise OSError("AFTER_SWITCH")
            return result

        with patch.object(mod.os, "replace", side_effect=crash):
            with self.assertRaisesRegex(OSError, "AFTER_SWITCH"):
                self.publish()
        new = self.current()
        self.assertNotEqual(old, new)
        self.assertEqual(self.pinned(new), new)
        self.assertEqual(self.pinned(old), old)

    def test_fsync_failure_after_swap_is_not_rollback(self):
        old = self.publish()
        sync = mod.sync_dir

        def injected(path):
            if Path(path) == self.root:
                raise OSError("ROOT_FSYNC_FAILED")
            return sync(path)

        with patch.object(mod, "sync_dir", side_effect=injected):
            with self.assertRaisesRegex(OSError, "ROOT_FSYNC_FAILED"):
                self.publish()
        new = self.current()
        self.assertNotEqual(new, old)
        self.assertEqual(self.pinned(new), new)
        # Physical power-loss durability remains untested.

    def test_receipt_mutation_is_rejected(self):
        gen = self.publish()
        receipt = self.root / "generations" / gen / "generation.json"
        receipt.write_bytes(b"{}")
        with self.assertRaises(mod.CompositionRefused):
            self.current()
        with self.assertRaises(mod.CompositionRefused):
            self.pinned(gen)

    def test_source_mutation_is_rejected(self):
        gen = self.publish()
        (self.root / "generations" / gen / "src/api/main.py").write_bytes(b"BAD")
        with self.assertRaises(mod.CompositionRefused):
            self.current()
        with self.assertRaises(mod.CompositionRefused):
            self.pinned(gen)

    def test_reader_pins_old_after_switch(self):
        old = self.publish()
        pinned_old = self.current()
        new = self.publish()
        self.assertEqual(self.current(), new)
        self.assertEqual(self.pinned(pinned_old), old)

    def test_gc_refuses_old_and_current_generations(self):
        old = self.publish()
        new = self.publish()
        for name in (old, new):
            with self.assertRaisesRegex(mod.CompositionRefused, "gc_disabled"):
                mod.retire_generation(self.root, name)
            self.assertEqual(self.pinned(name), name)

    def test_concurrent_writer_adds_unknown_file_between_stage_and_current(self):
        old = self.publish()
        rename = os.rename

        def injected(src, dst):
            result = rename(src, dst)
            if Path(dst).parent == self.root / "generations":
                (Path(dst) / "src/api/extra.py").write_bytes(b"EVIL")
            return result

        with patch.object(mod.os, "rename", side_effect=injected):
            with self.assertRaises(mod.CompositionRefused):
                self.publish()
        self.assertEqual(self.current(), old)

    def test_concurrent_writer_modifies_bytes_during_rename(self):
        old = self.publish()
        rename = os.rename

        def injected(src, dst):
            result = rename(src, dst)
            if Path(dst).parent == self.root / "generations":
                (Path(dst) / "src/api/main.py").write_bytes(b"EVIL")
            return result

        with patch.object(mod.os, "rename", side_effect=injected):
            with self.assertRaises(mod.CompositionRefused):
                self.publish()
        self.assertEqual(self.current(), old)

    def test_concurrent_directory_relocation_refuses(self):
        old = self.publish()
        rename = os.rename
        relocated = self.sandbox / "relocated"

        def injected(src, dst):
            if Path(src).parent == self.root / ".staging":
                rename(src, relocated)
            return rename(src, dst)

        with patch.object(mod.os, "rename", side_effect=injected):
            with self.assertRaises(OSError):
                self.publish()
        self.assertEqual(self.current(), old)
        self.assertEqual((self.live / "worker.py").read_bytes(), b"PROTECTED")

    def test_late_partial_file_write_keeps_previous_current(self):
        old = self.publish()

        def fail(stage, path):
            if stage == "after_file" and Path(path).name == "test_fixture.py":
                raise OSError("LATE_WRITE")

        with self.assertRaisesRegex(OSError, "LATE_WRITE"):
            self.publish(fault=fail)
        self.assertEqual(self.current(), old)

    def test_same_uid_writer_after_publish_corrupts_snapshot(self):
        gen = self.publish()
        (self.root / "generations" / gen / "src/api/main.py").write_bytes(
            b"HOSTILE_SAME_UID")
        with self.assertRaises(mod.CompositionRefused):
            self.pinned(gen)
        # This is detection, not prevention of hostile concurrent modification.

    def test_forged_partial_generation_is_not_accepted(self):
        old = self.publish()
        forged = "f" * 32
        dest = self.root / "generations" / forged
        dest.mkdir()
        (dest / "generation.json").write_bytes(b'{"status":"VERIFIED"}')
        (self.root / "CURRENT").write_bytes(mod.canonical({
            "generation": forged, "tree_sha256": "0" * 64,
            "manifest_sha256": self.pin}))
        with self.assertRaises(mod.CompositionRefused):
            self.current()
        self.assertEqual(self.pinned(old), old)


if __name__ == "__main__":
    unittest.main()
