"""CI-only exact MASTER runtime receipt -> OPS private generation integration.

Only the 30 frozen reviewed src/frontend overlays are published. This is not
a complete BASE checkout, an external Git attestation, or an installable app.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import ops_private_generation_prototype as ops

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "docs/ops_master_current_runtime_frozen_3eb400b9.json"
MASTER = "3eb400b9d8eafe9268787dc2744721e1b83b858a"
BASE = "3b2eef8a4139195db1a775545e83f1c1f58bf403"
MASTER_SCRIPT = "scripts/master_current_runtime_candidate.py"
MASTER_ALLOWLIST = "docs/coordination/master-reviewed-producer-files.json"
RECEIPT_CANONICAL_SHA256 = "b8b867647eaac221a4c899045499e3a8cb4cbb273a55ef61ed756be7829f2902"


def git(*args: str) -> bytes:
    return subprocess.check_output(("git", *args), cwd=ROOT, stderr=subprocess.PIPE)


def tracked_checkout_snapshot() -> str:
    """Track both indexed content and untracked status without writing files."""
    sha = hashlib.sha256()
    sha.update(git("status", "--porcelain", "--untracked-files=all", "-z"))
    for rel in filter(None, git("ls-files", "-z").split(b"\0")):
        path = ROOT / os.fsdecode(rel)
        sha.update(rel)
        if path.is_symlink():
            sha.update(b"symlink\0" + os.fsencode(os.readlink(path)))
        elif path.is_file():
            sha.update(b"regular\0" + hashlib.sha256(path.read_bytes()).digest())
        else:
            sha.update(b"missing\0")
    return sha.hexdigest()


class MasterRuntimePrivateGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if (
            os.environ.get("CI") != "true"
            or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_OS") != "Linux"
        ):
            raise unittest.SkipTest("disposable Linux GitHub Actions only")
        cls.before = tracked_checkout_snapshot()
        cls.review = json.loads(LEDGER.read_bytes())
        evidence = cls.review["evidence"]
        cls.pins = cls.review["source_pins"]
        if (
            evidence["commit"] != MASTER
            or evidence["workflow_id"] != 43618643208
            or cls.review["base"] != BASE
            or cls.review["admission"] != "CI_ONLY_NOT_PUBLISHED"
            or cls.review["runtime_blob_count"] != 30
            or cls.review["full_baseline_tree_included"]
            or cls.review["externally_attested"]
        ):
            raise AssertionError("frozen MASTER evidence mismatch")
        for sha in (BASE, MASTER, *cls.pins.values()):
            if git("rev-parse", sha + "^{commit}").decode().strip() != sha:
                raise AssertionError("pinned commit missing: " + sha)
        raw_master = git("show", MASTER + ":" + MASTER_SCRIPT)
        raw_allowlist = git("show", MASTER + ":" + MASTER_ALLOWLIST)
        if (
            ops.git_hash(raw_master) != cls.review["master_composer_git_blob_sha1"]
            or ops.git_hash(raw_allowlist) != cls.review["master_allowlist_git_blob_sha1"]
        ):
            raise AssertionError("MASTER Git blob identity mismatch")
        if ops.git_hash(git("show", "HEAD:" + MASTER_ALLOWLIST)) != cls.review[
            "master_allowlist_git_blob_sha1"
        ]:
            raise AssertionError("OPS worktree source allowlist diverged from MASTER")
        allowed = json.loads(raw_allowlist)["producers"]
        prepared = {}
        for label, commit in cls.pins.items():
            statuses = git(
                "diff", "--name-status", "--no-renames", BASE, commit
            ).decode().splitlines()
            changed = {}
            for row in statuses:
                status, path = row.split("\t", 1)
                if status not in ("A", "M") or path in changed:
                    raise AssertionError("unreviewed changed status or duplicate")
                changed[path] = status
            for path in sorted(allowed[label]):
                if not path.startswith(("src/", "frontend/")):
                    continue
                if path not in changed:
                    raise AssertionError("approved path missing in producer diff")
                line = git("ls-tree", commit, "--", path).decode().strip()
                parts = line.split()
                if (
                    len(parts) != 4
                    or parts[0] not in ("100644", "100755")
                    or parts[1] != "blob"
                    or parts[3] != path
                ):
                    raise AssertionError("source not regular Git file: " + path)
                raw = git("show", commit + ":" + path)
                if ops.git_hash(raw) != parts[2]:
                    raise AssertionError("source Git blob bytes mismatch")
                old = prepared.get(path)
                if old is not None and old[:2] != (parts[2], parts[0]):
                    raise AssertionError("cross-producer runtime conflict: " + path)
                # Same provenance winner as the frozen MASTER staging script.
                prepared[path] = (parts[2], parts[0], raw, label)
        expected = cls.review["runtime_files"]
        if len(prepared) != 30 or set(prepared) != set(expected):
            raise AssertionError("MASTER 30-file runtime set changed")
        for path, (sha, mode, _raw, label) in prepared.items():
            if expected[path] != {"blob": sha, "mode": mode, "producer": label}:
                raise AssertionError("frozen MASTER receipt differs: " + path)
        cls.source = prepared
        cls.blobs = [
            ops.ReviewedBlob(
                path=path,
                data=value[2],
                sha256=ops.hash256(value[2]),
                git_blob_sha1=value[0],
                mode=int(value[1][-3:], 8),
            )
            for path, value in sorted(prepared.items())
        ]
        cls.manifest = ops.canonical(
            {
                "schema": ops.SCHEMA,
                "source_pins": cls.pins,
                "files": [
                    {
                        "path": b.path,
                        "sha256": b.sha256,
                        "git_blob_sha1": b.git_blob_sha1,
                        "mode": b.mode,
                    }
                    for b in cls.blobs
                ],
            }
        )
        cls.manifest_sha256 = ops.hash256(cls.manifest)
        cls.expected_master_receipt = {
            "schema": "master-current-runtime-source-smoke/1",
            "status": "CI_ONLY_NOT_PUBLISHED",
            "source_pins": cls.pins,
            "runtime_files": expected,
        }
        if (
            ops.hash256(ops.canonical(cls.expected_master_receipt))
            != RECEIPT_CANONICAL_SHA256
        ):
            raise AssertionError("frozen MASTER receipt digest changed")

        # Re-execute precisely the source script whose previous CI was green.
        # It uses only an owner-0700 disposable tree and produces a receipt.
        with tempfile.TemporaryDirectory(prefix="ops-master-source-") as temp:
            source_file = Path(temp) / "master_current_runtime_candidate.py"
            source_file.write_bytes(raw_master)
            spec = importlib.util.spec_from_file_location(
                "master_exact_current_source_3eb400b9", source_file
            )
            if spec is None or spec.loader is None:
                raise AssertionError("cannot load pinned MASTER source")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.ROOT = ROOT
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                module.main()
            reconstructed, end = json.JSONDecoder().raw_decode(output.getvalue())
            if output.getvalue()[end:].strip() != (
                "NO_ACTIVE_CHECKOUT_WRITE_NO_NATIVE_LAUNCHER_NO_RELEASE"
            ):
                raise AssertionError("unexpected MASTER staging action")
            if reconstructed != cls.expected_master_receipt:
                raise AssertionError("MASTER script receipt diverged")
        print("OPS_CURRENT_MASTER_FROZEN_RECEIPT_SHA256", RECEIPT_CANONICAL_SHA256)
        print("OPS_CURRENT_PRIVATE_MANIFEST_SHA256", cls.manifest_sha256)
        print("OPS_CURRENT_RUNTIME_BLOBS", len(cls.blobs))

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "before"):
            if tracked_checkout_snapshot() != cls.before:
                raise AssertionError("ACTIVE_CHECKOUT_MUTATED")

    def private_workspace(self):
        parent = tempfile.TemporaryDirectory(prefix="ops-runtime-private-")
        self.addCleanup(parent.cleanup)
        root = Path(parent.name) / "private"
        root.mkdir(mode=0o700)
        return root, Path(parent.name)

    def publish(self, root, **kwargs):
        return ops.publish_generation(
            root,
            blobs=self.blobs,
            manifest=self.manifest,
            manifest_sha256=self.manifest_sha256,
            source_pins=self.pins,
            **kwargs,
        )

    def current(self, root):
        return ops.read_current(
            root, manifest=self.manifest, manifest_sha256=self.manifest_sha256
        )

    def pinned(self, root, generation):
        return ops.read_pinned(
            root, generation, manifest=self.manifest, manifest_sha256=self.manifest_sha256
        )

    def test_01_real_master_receipt_and_checkout_identity(self):
        self.assertEqual(len(self.blobs), 30)
        self.assertEqual(len(self.source), 30)
        self.assertEqual(tracked_checkout_snapshot(), self.before)

    def test_02_whole_generation_current_and_receipt(self):
        root, _parent = self.private_workspace()
        gen = self.publish(root)
        self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
        self.assertEqual(self.current(root), gen)
        self.assertEqual(self.pinned(root, gen), gen)
        receipt = json.loads((root / "generations" / gen / "generation.json").read_bytes())
        self.assertEqual(receipt["status"], "VERIFIED")
        self.assertEqual(receipt["manifest_sha256"], self.manifest_sha256)
        self.assertEqual(receipt["source_pins"], self.pins)
        expected_tree_sha = ops.verify_tree(root / "generations" / gen, self.blobs, sealed=True)
        self.assertEqual(receipt["tree_sha256"], expected_tree_sha)
        for blob in self.blobs:
            path = root / "generations" / gen / blob.path
            self.assertEqual(ops.hash256(path.read_bytes()), blob.sha256)
            self.assertEqual(stat.S_IMODE(path.lstat().st_mode), blob.mode)

    def test_03_before_current_failure_does_not_publish_partial_tree(self):
        root, _parent = self.private_workspace()
        old = self.publish(root)

        def fail(stage, _path):
            if stage == "after_publish_before_current":
                raise OSError("PRE_CURRENT_INJECTED")

        with self.assertRaisesRegex(OSError, "PRE_CURRENT_INJECTED"):
            self.publish(root, fault=fail)
        self.assertEqual(self.current(root), old)
        report = ops.inspect_recovery(
            root, manifest=self.manifest, manifest_sha256=self.manifest_sha256
        )
        self.assertEqual(report["state"], "CURRENT_VALID")
        self.assertEqual(len(report["verified"]), 2)
        self.assertFalse(report["gc_allowed"])
        self.assertFalse(report["auto_switch"])

    def test_04_after_current_swap_retains_old_pinned_generation(self):
        root, _parent = self.private_workspace()
        old = self.publish(root)
        replace = os.replace

        def fail(src, dst):
            result = replace(src, dst)
            if Path(dst).name == "CURRENT":
                raise OSError("POST_CURRENT_INJECTED")
            return result

        with mock.patch.object(ops.os, "replace", side_effect=fail):
            with self.assertRaisesRegex(OSError, "POST_CURRENT_INJECTED"):
                self.publish(root)
        new = self.current(root)
        self.assertNotEqual(new, old)
        self.assertEqual(self.pinned(root, old), old)
        self.assertEqual(self.pinned(root, new), new)
        for item in (old, new):
            with self.assertRaisesRegex(ops.CompositionRefused, "gc_disabled"):
                ops.retire_generation(root, item)

    def test_05_root_fsync_failure_visible_is_not_durable(self):
        root, _parent = self.private_workspace()
        old = self.publish(root)
        original = ops.sync_dir

        def fail(path):
            if Path(path) == root:
                raise OSError("POST_SWAP_FSYNC_FAILURE")
            return original(path)

        with mock.patch.object(ops, "sync_dir", side_effect=fail):
            with self.assertRaisesRegex(OSError, "POST_SWAP_FSYNC_FAILURE"):
                self.publish(root)
        new = self.current(root)
        self.assertNotEqual(new, old)
        self.assertEqual(self.pinned(root, new), new)
        # Physical crash durability remains UNKNOWN; this is only visible state.

    def test_06_corrupted_receipt_and_bytes_refused(self):
        root, _parent = self.private_workspace()
        gen = self.publish(root)
        path = root / "generations" / gen / "generation.json"
        original = path.read_bytes()
        path.write_bytes(b"{}")
        with self.assertRaises(ops.CompositionRefused):
            self.current(root)
        path.write_bytes(original)
        source = root / "generations" / gen / self.blobs[0].path
        source.write_bytes(b"CORRUPTED")
        with self.assertRaises(ops.CompositionRefused):
            self.pinned(root, gen)

    def test_07_symlink_substitution_rejected_without_external_write(self):
        root, parent = self.private_workspace()
        sentinel = parent / "sentinel"
        sentinel.write_bytes(b"UNCHANGED")

        def inject(stage, path):
            if stage == "after_file":
                path.unlink()
                path.symlink_to(sentinel)

        with self.assertRaises(ops.CompositionRefused):
            self.publish(root, fault=inject)
        self.assertEqual(sentinel.read_bytes(), b"UNCHANGED")
        self.assertFalse((root / "CURRENT").exists())

    def test_08_hardlink_alias_rejected_without_truncation(self):
        root, parent = self.private_workspace()
        alias = parent / "outside-hardlink"

        def inject(stage, path):
            if stage == "after_file" and not alias.exists():
                os.link(path, alias)

        with self.assertRaises(ops.CompositionRefused):
            self.publish(root, fault=inject)
        self.assertEqual(alias.read_bytes(), self.blobs[0].data)
        self.assertFalse((root / "CURRENT").exists())

    def test_09_same_uid_file_mutation_at_publish_boundary_refused(self):
        root, _parent = self.private_workspace()
        old = self.publish(root)
        rename = os.rename

        def inject(src, dst):
            result = rename(src, dst)
            if Path(dst).parent == root / "generations":
                (Path(dst) / self.blobs[0].path).write_bytes(b"HOSTILE_MUTATION")
            return result

        with mock.patch.object(ops.os, "rename", side_effect=inject):
            with self.assertRaises(ops.CompositionRefused):
                self.publish(root)
        self.assertEqual(self.current(root), old)

    def test_10_relocated_staging_directory_cannot_update_current(self):
        root, parent = self.private_workspace()
        old = self.publish(root)
        relocation = parent / "relocated"

        def inject(stage, candidate):
            if stage == "before_publish":
                candidate.rename(relocation)

        with self.assertRaises(ops.CompositionRefused):
            self.publish(root, fault=inject)
        self.assertEqual(self.current(root), old)
        self.assertTrue(relocation.exists())

    def test_11_unknown_file_and_untrusted_blob_rejected(self):
        root, _parent = self.private_workspace()

        def inject(stage, candidate):
            if stage == "after_verify":
                (candidate / "frontend" / "UNKNOWN").write_bytes(b"UNAPPROVED")

        with self.assertRaises(ops.CompositionRefused):
            self.publish(root, fault=inject)
        self.assertFalse((root / "CURRENT").exists())
        original = self.blobs[0]
        forged = ops.ReviewedBlob(
            original.path,
            b"FORGED",
            original.sha256,
            original.git_blob_sha1,
            original.mode,
        )
        with self.assertRaises(ops.CompositionRefused):
            ops.verify_sources(
                [forged, *self.blobs[1:]],
                self.manifest,
                self.manifest_sha256,
                self.pins,
            )

    def test_12_repeated_current_switch_no_gc_no_launcher(self):
        root, _parent = self.private_workspace()
        with mock.patch("subprocess.Popen", side_effect=AssertionError("process")):
            with mock.patch("os.system", side_effect=AssertionError("shell")):
                old = self.publish(root)
                new = self.publish(root)
        self.assertEqual(self.current(root), new)
        self.assertEqual(self.pinned(root, old), old)
        with self.assertRaises(ops.CompositionRefused):
            ops.retire_generation(root, old)
        self.assertEqual(tracked_checkout_snapshot(), self.before)


if __name__ == "__main__":
    unittest.main()
