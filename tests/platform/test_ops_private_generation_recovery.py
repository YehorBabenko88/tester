"""Fail-closed, read-only crash recovery gates for private generations.

No real power cuts, native launcher, Windows services, or PostgreSQL involved.
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import ops_private_generation_prototype as mod


class ReadOnlyRecoveryTests(unittest.TestCase):
    def setUp(self):
        sandbox = TemporaryDirectory()
        self.addCleanup(sandbox.cleanup)
        self.base = Path(sandbox.name)
        self.root = self.base / "owner-only"
        self.root.mkdir(mode=0o700)
        self.root.chmod(0o700)
        self.external = self.base / "protected"
        self.external.write_bytes(b"PRESERVE")
        self.pins = {"BASE": "1" * 40, "OPS": "2" * 40}
        raw = b"REVIEWED"
        self.blobs = [mod.ReviewedBlob("src/approved.py", raw,
                                       mod.hash256(raw), mod.git_hash(raw))]
        self.manifest = mod.canonical({
            "schema": mod.SCHEMA, "source_pins": self.pins,
            "files": [dict(path=b.path, sha256=b.sha256,
                           git_blob_sha1=b.git_blob_sha1, mode=b.mode)
                      for b in self.blobs],
        })
        self.digest = mod.hash256(self.manifest)

    def publish(self, **kwargs):
        return mod.publish_generation(self.root, blobs=self.blobs,
                                      manifest=self.manifest,
                                      manifest_sha256=self.digest,
                                      source_pins=self.pins, **kwargs)

    def recover(self):
        return mod.inspect_recovery(self.root, manifest=self.manifest,
                                    manifest_sha256=self.digest)

    def current(self):
        return mod.read_current(self.root, manifest=self.manifest,
                                manifest_sha256=self.digest)

    def test_01_empty_workspace_is_not_automatically_activated(self):
        report = self.recover()
        self.assertEqual(report["state"], "CURRENT_MISSING")
        self.assertFalse(report["auto_switch"])
        self.assertFalse(report["gc_allowed"])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_02_before_current_failure_preserves_unreferenced_generation(self):
        old = self.publish()

        def fault(stage, _):
            if stage == "after_publish_before_current":
                raise OSError("BEFORE_CURRENT")

        with self.assertRaisesRegex(OSError, "BEFORE_CURRENT"):
            self.publish(fault=fault)
        report = self.recover()
        self.assertEqual(report["state"], "CURRENT_VALID")
        self.assertEqual(report["current"], old)
        self.assertEqual(len(report["verified"]), 2)
        self.assertEqual(len(list((self.root / "generations").iterdir())), 2)
        self.assertEqual(self.external.read_bytes(), b"PRESERVE")

    def test_03_after_current_swap_failure_is_ambiguous_not_rolled_back(self):
        old = self.publish()
        actual = os.replace

        def replace(src, dst):
            actual(src, dst)
            if Path(dst).name == "CURRENT":
                raise OSError("AFTER_SWAP")

        with patch.object(mod.os, "replace", side_effect=replace):
            with self.assertRaisesRegex(OSError, "AFTER_SWAP"):
                self.publish()
        report = self.recover()
        self.assertEqual(report["state"], "CURRENT_VALID")
        self.assertNotEqual(report["current"], old)
        self.assertEqual(len(report["verified"]), 2)
        self.assertFalse(report["auto_switch"])

    def test_04_root_fsync_error_does_not_delete_new_generation(self):
        old = self.publish()
        real = mod.sync_dir

        def fault(path):
            if Path(path) == self.root:
                raise OSError("FSYNC_FAILED")
            return real(path)

        with patch.object(mod, "sync_dir", side_effect=fault):
            with self.assertRaisesRegex(OSError, "FSYNC_FAILED"):
                self.publish()
        report = self.recover()
        self.assertNotEqual(report["current"], old)
        self.assertEqual(report["state"], "CURRENT_VALID")
        self.assertEqual(len(report["verified"]), 2)

    def test_05_missing_pointer_no_auto_promote_even_valid_generation(self):
        gen = self.publish()
        (self.root / "CURRENT").unlink()
        report = self.recover()
        self.assertEqual(report["state"], "CURRENT_MISSING")
        self.assertIn(gen, report["verified"])
        self.assertFalse((self.root / "CURRENT").exists())
        self.assertEqual(mod.recovery_candidate(
            self.root, gen, manifest=self.manifest,
            manifest_sha256=self.digest), gen)

    def test_06_bad_pointer_no_fallback_to_valid_previous_generation(self):
        old = self.publish()
        new = self.publish()
        (self.root / "generations" / new / "src/approved.py").write_bytes(b"CORRUPTED")
        report = self.recover()
        self.assertEqual(report["state"], "CURRENT_INVALID")
        self.assertIn(old, report["verified"])
        self.assertIn(new, report["unverified"])
        with self.assertRaises(mod.CompositionRefused):
            self.current()
        self.assertFalse(report["auto_switch"])

    def test_07_receipt_tampering_refused_for_current_and_manual_candidate(self):
        gen = self.publish()
        (self.root / "generations" / gen / "generation.json").write_bytes(b"{}")
        report = self.recover()
        self.assertEqual(report["state"], "CURRENT_INVALID")
        self.assertIn(gen, report["unverified"])
        with self.assertRaises(mod.CompositionRefused):
            mod.recovery_candidate(self.root, gen, manifest=self.manifest,
                                   manifest_sha256=self.digest)

    def test_08_staging_partial_tree_preserved_and_never_promoted(self):
        def fault(stage, path):
            if stage == "after_file":
                raise OSError("PARTIAL_WRITE")
        with self.assertRaisesRegex(OSError, "PARTIAL_WRITE"):
            self.publish(fault=fault)
        report = self.recover()
        self.assertEqual(report["state"], "CURRENT_MISSING")
        self.assertEqual(len(report["staging"]), 1)
        self.assertEqual(report["verified"], [])
        self.assertFalse((self.root / "CURRENT").exists())

    def test_09_failed_pointer_replace_preserves_evidence_and_old_current(self):
        old = self.publish()
        replace = os.replace

        def fault(src, dst):
            if Path(dst).name == "CURRENT":
                raise OSError("CURRENT_WRITE")
            return replace(src, dst)
        with patch.object(mod.os, "replace", side_effect=fault):
            with self.assertRaisesRegex(OSError, "CURRENT_WRITE"):
                self.publish()
        report = self.recover()
        self.assertEqual(report["current"], old)
        self.assertEqual(len(report["verified"]), 2)
        self.assertEqual(len(report["pointer_temps"]), 1)
        self.assertIn("unfinished_pointer_write", report["problems"])

    def test_10_refuse_gc_of_current_previous_and_orphan(self):
        first = self.publish()
        second = self.publish()
        with self.assertRaises(OSError):
            self.publish(fault=lambda stage, _: (
                (_ for _ in ()).throw(OSError("ORPHAN"))
                if stage == "after_publish_before_current" else None))
        names = [p.name for p in (self.root / "generations").iterdir()]
        self.assertEqual(len(names), 3)
        for name in names:
            with self.assertRaisesRegex(mod.CompositionRefused, "gc_disabled"):
                mod.retire_generation(self.root, name)
        self.assertEqual(self.current(), second)
        self.assertIn(first, names)

    def test_11_symlink_generation_is_reported_unverified(self):
        good = self.publish()
        evil = "a" * 32
        (self.root / "generations" / evil).symlink_to(self.base)
        report = self.recover()
        self.assertEqual(report["current"], good)
        self.assertIn(evil, report["unverified"])
        self.assertEqual(self.external.read_bytes(), b"PRESERVE")

    def test_12_unexpected_workspace_entry_is_reported_not_deleted(self):
        gen = self.publish()
        extra = self.root / "unknown.sh"
        extra.write_bytes(b"PRESERVE")
        report = self.recover()
        self.assertEqual(report["current"], gen)
        self.assertIn("unexpected_root_entries", report["problems"])
        self.assertEqual(extra.read_bytes(), b"PRESERVE")

    def test_13_root_manifest_pin_mismatch_does_not_touch_pointer(self):
        gen = self.publish()
        original = (self.root / "CURRENT").read_bytes()
        with self.assertRaisesRegex(mod.CompositionRefused, "untrusted_manifest"):
            mod.inspect_recovery(self.root, manifest=self.manifest,
                                 manifest_sha256="0" * 64)
        self.assertEqual((self.root / "CURRENT").read_bytes(), original)
        self.assertEqual(self.current(), gen)

    def test_14_reader_pin_revalidates_old_without_following_current(self):
        old = self.publish()
        new = self.publish()
        self.assertEqual(mod.recovery_candidate(
            self.root, old, manifest=self.manifest,
            manifest_sha256=self.digest), old)
        self.assertEqual(self.current(), new)
        self.assertEqual(self.external.read_bytes(), b"PRESERVE")

    def test_15_recovery_never_uses_launcher_or_process(self):
        self.publish()
        with patch("subprocess.Popen", side_effect=AssertionError("process")):
            with patch("os.system", side_effect=AssertionError("shell")):
                self.assertEqual(self.recover()["state"], "CURRENT_VALID")


if __name__ == "__main__":
    unittest.main()
