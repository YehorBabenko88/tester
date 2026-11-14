"""Pure filesystem negatives for private generation prototype. Never touches PC4."""
from __future__ import annotations

import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.ops_private_generation_prototype import (
    SCHEMA,
    CompositionRefused,
    ReviewedBlob,
    canonical,
    git_hash,
    hash256,
    publish_generation,
    read_current,
)


class PrivateGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sandbox = Path(self.temp.name)
        self.private = self.sandbox / "private"
        self.private.mkdir(mode=0o700)
        self.private.chmod(0o700)
        self.active_checkout = self.sandbox / "active-checkout"
        self.active_checkout.mkdir()
        (self.active_checkout / "worker.py").write_bytes(b"PC4_PROTECTED")
        self.pins = {"BASE": "1" * 40, "OPS": "2" * 40}
        self.blobs = [
            self.blob("src/api/main.py", b"VERSION_A"),
            self.blob("tests/platform/test_x.py", b"TEST_A"),
        ]
        self.manifest, self.pin = self.make_manifest(self.blobs)

    @staticmethod
    def blob(path, data):
        return ReviewedBlob(path, data, hash256(data), git_hash(data))

    def make_manifest(self, blobs):
        manifest = canonical({
            "schema": SCHEMA,
            "source_pins": self.pins,
            "files": [
                {"path": b.path, "sha256": b.sha256,
                 "git_blob_sha1": b.git_blob_sha1, "mode": b.mode}
                for b in blobs
            ],
        })
        return manifest, hash256(manifest)

    def publish(self, **overrides):
        return publish_generation(
            self.private,
            blobs=overrides.pop("blobs", self.blobs),
            manifest=overrides.pop("manifest", self.manifest),
            manifest_sha256=overrides.pop("manifest_sha256", self.pin),
            source_pins=overrides.pop("source_pins", self.pins),
            **overrides,
        )

    def current(self):
        return read_current(self.private, manifest=self.manifest,
                            manifest_sha256=self.pin)

    def test_01_complete_generation_0700_and_active_checkout_unchanged(self):
        generation = self.publish()
        self.assertEqual(self.current(), generation)
        self.assertEqual((self.private / "generations" / generation
                          / self.blobs[0].path).read_bytes(), b"VERSION_A")
        self.assertEqual((self.active_checkout / "worker.py").read_bytes(), b"PC4_PROTECTED")
        for name in ("private", "private/generations", "private/.staging"):
            self.assertEqual(stat.S_IMODE((self.sandbox / name).stat().st_mode), 0o700)

    def test_02_forged_manifest_revision_and_git_data_refused(self):
        with self.assertRaisesRegex(CompositionRefused, "untrusted_manifest"):
            self.publish(manifest=self.manifest + b" ")
        forged = self.blob(self.blobs[0].path, b"FORGED")
        with self.assertRaisesRegex(CompositionRefused, "reviewed_blob_mismatch"):
            self.publish(blobs=[forged, self.blobs[1]])
        with self.assertRaisesRegex(CompositionRefused, "bad_manifest_schema"):
            self.publish(source_pins={"BASE": "9" * 40})
        self.assertEqual(list(self.private.iterdir()), [])

    def test_03_missing_duplicate_extra_and_traversal_refused(self):
        with self.assertRaisesRegex(CompositionRefused, "missing_reviewed_source"):
            self.publish(blobs=[self.blobs[0]])
        with self.assertRaisesRegex(CompositionRefused, "duplicate_or_unknown_source"):
            self.publish(blobs=self.blobs + [self.blobs[0]])
        with self.assertRaisesRegex(CompositionRefused, "duplicate_or_unknown_source"):
            self.publish(blobs=self.blobs + [self.blob("scripts/unapproved.py", b"X")])
        bad = self.blob("src/../worker.py", b"X")
        manifest, pin = self.make_manifest([bad])
        with self.assertRaisesRegex(CompositionRefused, "invalid_or_traversing_path"):
            self.publish(blobs=[bad], manifest=manifest, manifest_sha256=pin)

    def test_04_private_root_symlink_and_unknown_checkout_file_refused(self):
        self.private.chmod(0o755)
        with self.assertRaisesRegex(CompositionRefused, "workspace_not_owner_only_0700"):
            self.publish()
        self.private.chmod(0o700)
        (self.private / "unknown.sh").write_bytes(b"UNAPPROVED")
        with self.assertRaisesRegex(CompositionRefused, "unexpected_workspace_file"):
            self.publish()

    def test_05_symlink_staging_file_refused_before_publication(self):
        sentinel = self.sandbox / "outside"
        sentinel.write_bytes(b"KEEP")

        def fault(stage, p):
            if stage == "after_file" and p.name == "main.py":
                p.unlink()
                p.symlink_to(sentinel)

        with self.assertRaisesRegex(CompositionRefused, "unsafe_staging_inode"):
            self.publish(fault=fault)
        self.assertEqual(sentinel.read_bytes(), b"KEEP")
        self.assertFalse((self.private / "CURRENT").exists())

    def test_06_hardlink_to_external_inode_detected(self):
        external = self.sandbox / "outside-link"
        def fault(stage, p):
            if stage == "after_file" and p.name == "main.py":
                os.link(p, external)
        with self.assertRaisesRegex(CompositionRefused, "unsafe_staging_inode"):
            self.publish(fault=fault)
        self.assertEqual(external.read_bytes(), b"VERSION_A")
        self.assertFalse((self.private / "CURRENT").exists())

    def test_07_in_place_byte_mutation_detected_before_seal(self):
        def fault(stage, stage_path):
            if stage == "after_verify":
                (stage_path / "src/api/main.py").write_bytes(b"MODIFIED")
        with self.assertRaisesRegex(CompositionRefused, "corrupted_staging_blob"):
            self.publish(fault=fault)
        self.assertFalse((self.private / "CURRENT").exists())

    def test_08_unknown_file_or_empty_directory_in_staging_refused(self):
        for injected in ("file", "directory"):
            with self.subTest(injected=injected):
                def fault(stage, p):
                    if stage == "after_verify":
                        if injected == "file":
                            (p / "src/unknown.txt").write_bytes(b"UNKNOWN")
                        else:
                            (p / "src/empty-surprise").mkdir()
                with self.assertRaises(CompositionRefused):
                    self.publish(fault=fault)
                self.assertFalse((self.private / "CURRENT").exists())

    def test_09_fault_at_each_stage_does_not_publish(self):
        for point in ("after_file", "after_verify", "before_publish"):
            with self.subTest(point=point):
                def fault(stage, p):
                    if stage == point:
                        raise OSError("INJECTED_DISK_FAILURE")
                with self.assertRaisesRegex(OSError, "INJECTED_DISK_FAILURE"):
                    self.publish(fault=fault)
                self.assertFalse((self.private / "CURRENT").exists())

    def test_10_failed_later_generation_keeps_previous_current(self):
        first = self.publish()
        def fault(stage, _):
            if stage == "after_publish_before_current":
                raise OSError("INJECTED_LATE_RENAME_FAILURE")
        with self.assertRaisesRegex(OSError, "INJECTED_LATE_RENAME_FAILURE"):
            self.publish(fault=fault)
        self.assertEqual(self.current(), first)

    def test_11_corrupt_current_generation_denied(self):
        first = self.publish()
        (self.private / "generations" / first / self.blobs[0].path).write_bytes(b"EVIL")
        with self.assertRaisesRegex(CompositionRefused, "incomplete_or_corrupted_generation"):
            self.current()

    def test_12_current_symlink_or_corrupted_receipt_denied(self):
        first = self.publish()
        p = self.private / "CURRENT"
        p.unlink()
        p.symlink_to(self.active_checkout / "worker.py")
        with self.assertRaisesRegex(CompositionRefused, "current_not_published"):
            self.current()
        p.unlink()
        p.write_bytes(canonical({"generation": first, "tree_sha256": "0" * 64,
                                 "manifest_sha256": self.pin}))
        with self.assertRaisesRegex(CompositionRefused, "incomplete_or_corrupted_generation"):
            self.current()

    def test_13_atomic_pointer_switch_keeps_prior_generation_intact(self):
        first = self.publish()
        second = self.publish()
        self.assertNotEqual(first, second)
        self.assertTrue((self.private / "generations" / first).is_dir())
        self.assertEqual(self.current(), second)
        self.assertEqual((self.active_checkout / "worker.py").read_bytes(), b"PC4_PROTECTED")

    def test_14_source_hash_prevents_untrusted_git_reader_substitution(self):
        modified = ReviewedBlob(self.blobs[0].path, b"FORGED",
                                self.blobs[0].sha256, self.blobs[0].git_blob_sha1)
        with self.assertRaisesRegex(CompositionRefused, "unverified_git_blob"):
            self.publish(blobs=[modified, self.blobs[1]])

    def test_15_forged_runner_env_not_treated_as_independent_attestation(self):
        # Conditional model: same env can be supplied by untrusted caller.
        # Prototype claims no cryptographic runner attestation.
        with patch.dict(os.environ, {"CI": "true", "GITHUB_ACTIONS": "true",
                                     "GITHUB_SHA": "9" * 40}, clear=False):
            gen = self.publish()
        self.assertEqual(self.current(), gen)

    def test_16_relocated_stage_refuses_publication(self):
        moved = self.sandbox / "relocated-stage"
        def fault(stage, p):
            if stage == "before_publish":
                p.rename(moved)
        with self.assertRaisesRegex(CompositionRefused, "incomplete_staging_tree"):
            self.publish(fault=fault)
        self.assertFalse((self.private / "CURRENT").exists())
        # A hostile same-UID relocator is outside the prototype trust model;
        # it may leave orphaned staged files even when no output was published.

    def test_17_no_launcher_invocation_or_windows_scm(self):
        with patch("subprocess.Popen", side_effect=AssertionError("no process")):
            with patch("os.system", side_effect=AssertionError("no shell")):
                gen = self.publish()
        self.assertEqual(self.current(), gen)


if __name__ == "__main__":
    unittest.main()
