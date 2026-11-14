"""Exact MASTER d6d6548 security audit in disposable, fully mocked Git sandboxes.

Every write is inside TemporaryDirectory; neither MASTER's real checkout nor
PC4, SCM, PostgreSQL, network or native launcher can be contacted.
Positive tests that reproduce a bypass are *vulnerability evidence*, not
claims that the underlying behavior is secure.
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import os
import subprocess
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

MASTER_SHA = "d6d6548fee441710e34f716d29adb4c25c7e4660"
EXPECTED_OPS_PIN = "0d4649f3a7cd3595ea58757f2abaf2f1b0558667"
PATH = "scripts/ops_native_scoped_stop.py"
PAYLOAD = b"DISPOSABLE_COMPOSER_TEST_ONLY"


class MasterV3SandboxTests(unittest.TestCase):
    def setUp(self):
        source_name = os.environ.get("OPS_MASTER_V3_COMPOSER_FILE")
        manifest_name = os.environ.get("OPS_MASTER_V3_MANIFEST_FILE")
        if not source_name or not manifest_name:
            self.skipTest("NOT_RUN: exact MASTER v3 source+manifest not checked out")
        self.source = Path(source_name)
        self.manifest_source = Path(manifest_name)
        self.assertTrue(self.source.is_file())
        self.assertTrue(self.manifest_source.is_file())
        self.assertFalse(self.source.is_symlink())
        self.assertFalse(self.manifest_source.is_symlink())
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sandbox = Path(self.temp.name).resolve()
        self.root = self.sandbox / "checkout"
        self.root.mkdir()
        self.outside = self.sandbox / "outside"
        self.outside.mkdir()
        manifest = json.loads(self.manifest_source.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], "master-reviewed-producer-files/1")
        self.assertIn(PATH, manifest["producers"]["OPS"])
        self.manifest = self.root / "docs/coordination/master-reviewed-producer-files.json"
        self.manifest.parent.mkdir(parents=True)
        self._save_manifest(manifest)

        spec = importlib.util.spec_from_file_location(
            "exact_master_v3_composer_sandbox", self.source
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.assertEqual(self.module.BASE, "3b2eef8a4139195db1a775545e83f1c1f58bf403")
        self.assertEqual(dict(self.module.PRODUCERS)["OPS"], EXPECTED_OPS_PIN)
        self.module.ROOT = self.root

    def _save_manifest(self, manifest):
        self.manifest.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    def _run(self, paths=(PATH,), *, status="A", mode="100644",
             kind="blob", env_overrides=None, contents=PAYLOAD):
        if isinstance(paths, str):
            paths = (paths,)
        calls = []

        def fake_git(*args):
            calls.append(args)
            if args[0] == "rev-parse":
                return (MASTER_SHA if args[1] == "HEAD" else args[1].split("^{")[0]).encode()
            if args[0] == "diff":
                if args[-1] == EXPECTED_OPS_PIN:
                    return "".join(f"{status}\t{p}\n" for p in paths).encode()
                return b""
            if args[0] == "ls-tree":
                return f"{mode} {kind} {'a' * 40}\t{args[-1]}\n".encode()
            if args[0] == "show":
                return contents
            raise AssertionError("unexpected real git operation: " + repr(args))

        env = {
            "CI": "true", "GITHUB_ACTIONS": "true",
            "GITHUB_WORKSPACE": str(self.root),
            "EXPECTED_STAGING_SHA": MASTER_SHA, "GITHUB_SHA": MASTER_SHA,
            "GITHUB_REPOSITORY": "YehorBabenko88/tester",
            "GITHUB_RUN_ID": "12345", "RUNNER_OS": "Linux",
        }
        env.update(env_overrides or {})
        with patch.object(self.module, "git", side_effect=fake_git):
            with patch.dict(os.environ, env, clear=True):
                with redirect_stdout(StringIO()) as output:
                    self.module.main()
                    return output.getvalue(), calls

    def test_01_regular_pinned_source_smoke_and_no_native_activation(self):
        with patch.object(subprocess, "Popen", side_effect=AssertionError("native Popen forbidden")):
            with patch.object(subprocess, "run", side_effect=AssertionError("native run forbidden")):
                with patch.object(os, "system", side_effect=AssertionError("shell forbidden")):
                    output, calls = self._run()
        self.assertIn("COMPOSITION_ONLY: not a commit", output)
        self.assertEqual((self.root / PATH).read_bytes(), PAYLOAD)
        self.assertTrue(any(args[0] == "ls-tree" for args in calls))
        tree = ast.parse(self.source.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                names.add(func.id if isinstance(func, ast.Name) else
                          func.attr if isinstance(func, ast.Attribute) else "<call>")
        self.assertFalse(names & {
            "Popen", "run", "system", "kill", "terminate", "StartService",
            "CreateProcess", "ShellExecute", "reboot", "runpy",
        })

    def test_02_parent_symlink_refused_before_write(self):
        (self.root / "scripts").symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(SystemExit):
            self._run()
        self.assertFalse((self.outside / Path(PATH).name).exists())

    def test_03_leaf_symlink_refused_before_write(self):
        (self.root / "scripts").mkdir()
        sentinel = self.outside / "sentinel.py"
        sentinel.write_text("KEEP", encoding="utf-8")
        (self.root / PATH).symlink_to(sentinel)
        with self.assertRaises(SystemExit):
            self._run()
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "KEEP")

    def test_04_symlink_race_before_leaf_open_refused_by_nofollow(self):
        (self.root / "scripts").mkdir()
        sentinel = self.outside / "race-sentinel.py"
        sentinel.write_text("KEEP", encoding="utf-8")
        original_open = os.open
        swapped = False

        def intercept(path, flags, *args, **kwargs):
            nonlocal swapped
            if not swapped and path == Path(PATH).name and flags & os.O_TRUNC:
                swapped = True
                (self.root / PATH).symlink_to(sentinel)
            return original_open(path, flags, *args, **kwargs)

        with patch.object(os, "open", side_effect=intercept):
            with self.assertRaises(OSError):
                self._run()
        self.assertTrue(swapped)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "KEEP")

    def test_05_parent_symlink_race_before_directory_open_refused(self):
        (self.root / "scripts").mkdir()
        original_open = os.open
        swapped = False

        def intercept(path, flags, *args, **kwargs):
            nonlocal swapped
            if not swapped and path == "scripts" and "dir_fd" in kwargs:
                swapped = True
                (self.root / "scripts").rmdir()
                (self.root / "scripts").symlink_to(self.outside, target_is_directory=True)
            return original_open(path, flags, *args, **kwargs)

        with patch.object(os, "open", side_effect=intercept):
            with self.assertRaises(OSError):
                self._run()
        self.assertTrue(swapped)
        self.assertFalse((self.outside / Path(PATH).name).exists())

    def test_06_hardlink_outside_checkout_is_overwritten_VULNERABILITY(self):
        (self.root / "scripts").mkdir()
        sentinel = self.outside / "hardlink-target.py"
        sentinel.write_text("KEEP", encoding="utf-8")
        try:
            os.link(sentinel, self.root / PATH)
        except OSError as exc:
            self.skipTest("hardlink not supported by test filesystem: " + str(exc))
        output, _ = self._run()
        self.assertIn("COMPOSITION_ONLY", output)
        self.assertEqual(sentinel.read_bytes(), PAYLOAD)  # real containment bypass

    def test_07_parent_directory_relocation_after_open_VULNERABILITY(self):
        (self.root / "scripts").mkdir()
        relocated = self.outside / "relocated-scripts"
        original_open = os.open
        swapped = False

        def intercept(path, flags, *args, **kwargs):
            nonlocal swapped
            if not swapped and path == Path(PATH).name and flags & os.O_TRUNC:
                swapped = True
                (self.root / "scripts").rename(relocated)
                (self.root / "scripts").mkdir()
            return original_open(path, flags, *args, **kwargs)

        with patch.object(os, "open", side_effect=intercept):
            output, _ = self._run()
        self.assertTrue(swapped)
        self.assertIn("COMPOSITION_ONLY", output)
        self.assertEqual((relocated / Path(PATH).name).read_bytes(), PAYLOAD)
        self.assertFalse((self.root / PATH).exists())

    def test_08_git_symlink_and_special_blob_modes_refused(self):
        for mode, kind in (
            ("120000", "blob"), ("160000", "commit"),
            ("040000", "tree"), ("100600", "blob"),
        ):
            with self.subTest(mode=mode), self.assertRaises(SystemExit):
                self._run(mode=mode, kind=kind)
        self.assertFalse((self.root / PATH).exists())

    def test_09_protected_paths_delete_traversal_extra_refused(self):
        for path in (
            "src/api/worker.py", "src/integration/pc4_runtime.py",
            "scripts/../src/api/worker.py", "scripts/../../outside/unsafe.py",
            "scripts/unreviewed_installer_hook.py", "/tmp/evil.py",
        ):
            with self.subTest(path=path), self.assertRaises(SystemExit):
                self._run(path)
        with self.assertRaises(SystemExit):
            self._run(status="D")
        self.assertFalse((self.outside / "unsafe.py").exists())

    def test_10_metadata_mismatches_refused(self):
        for name, value in (
            ("CI", "false"), ("GITHUB_ACTIONS", "false"),
            ("GITHUB_SHA", "0" * 40), ("GITHUB_REPOSITORY", "foreign/repo"),
            ("GITHUB_RUN_ID", "not-a-run"), ("RUNNER_OS", "Windows"),
            ("GITHUB_WORKSPACE", str(self.outside)),
            ("EXPECTED_STAGING_SHA", "f" * 40),
        ):
            with self.subTest(name=name), self.assertRaises(SystemExit):
                self._run(env_overrides={name: value})
        self.assertFalse((self.root / PATH).exists())

    def test_11_complete_environment_strings_are_forgeable_VULNERABILITY(self):
        # The fake Git adapter deliberately simulates a compromised runner.
        # Same valid strings can be set by any local process; no attestation.
        output, _ = self._run()
        self.assertIn("COMPOSITION_ONLY", output)

    def test_12_unreviewed_path_without_allowlist_modification_refused(self):
        with self.assertRaises(SystemExit):
            self._run("scripts/unreviewed_installer_hook.py")
        self.assertFalse((self.root / "scripts/unreviewed_installer_hook.py").exists())

    def test_13_modified_worktree_allowlist_accepts_unreviewed_path_VULNERABILITY(self):
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["producers"]["OPS"].append("scripts/unreviewed_installer_hook.py")
        self._save_manifest(manifest)
        output, _ = self._run("scripts/unreviewed_installer_hook.py")
        self.assertIn("COMPOSITION_ONLY", output)
        self.assertTrue((self.root / "scripts/unreviewed_installer_hook.py").is_file())

    def test_14_symlinked_worktree_allowlist_accepted_VULNERABILITY(self):
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        external = self.outside / "substituted-manifest.json"
        external.write_text(json.dumps(manifest), encoding="utf-8")
        self.manifest.unlink()
        self.manifest.symlink_to(external)
        output, _ = self._run()
        self.assertIn("COMPOSITION_ONLY", output)

    def test_15_corrupt_manifest_refused(self):
        self.manifest.write_text('{"schema": "wrong", "producers": {}}', encoding="utf-8")
        with self.assertRaises(SystemExit):
            self._run()
        self.assertFalse((self.root / PATH).exists())

    def test_16_unrelated_manifest_missing_item_not_detected_VULNERABILITY(self):
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        others = [x for x in manifest["producers"]["OPS"] if x != PATH]
        self.assertTrue(others)
        manifest["producers"]["OPS"].remove(others[0])
        self._save_manifest(manifest)
        output, _ = self._run()
        self.assertIn("COMPOSITION_ONLY", output)
        self.assertTrue((self.root / PATH).is_file())

    def test_17_unexpected_workspace_file_not_rejected(self):
        unexpected = self.root / "scripts/unknown_preexisting_helper.py"
        unexpected.parent.mkdir()
        unexpected.write_text("UNREVIEWED", encoding="utf-8")
        output, _ = self._run()
        self.assertIn("COMPOSITION_ONLY", output)
        self.assertEqual(unexpected.read_text(encoding="utf-8"), "UNREVIEWED")

    def test_19_hardlink_alias_overwrites_protected_worker_VULNERABILITY(self):
        # Exact protected path is not in allowlist, but its inode can be reached
        # through a regular, approved hardlink. All files are disposable.
        protected = self.root / "src/api/worker.py"
        protected.parent.mkdir(parents=True)
        protected.write_text("PROTECTED", encoding="utf-8")
        (self.root / "scripts").mkdir()
        try:
            os.link(protected, self.root / PATH)
        except OSError as exc:
            self.skipTest("hardlink unavailable in isolated test FS: " + str(exc))
        output, _ = self._run()
        self.assertIn("COMPOSITION_ONLY", output)
        self.assertEqual(protected.read_bytes(), PAYLOAD)

    def test_20_mid_write_failure_leaves_partial_composition_VULNERABILITY(self):
        # Preflight cannot promise rollback if a later descriptor-relative
        # write fails. The first approved file remains modified.
        second = "docs/ops_portable_checkpoint.md"
        self.assertIn(second, json.loads(self.manifest.read_text())["producers"]["OPS"])
        original_open = os.open
        refused = False

        def intercept(path, flags, *args, **kwargs):
            nonlocal refused
            if path == "ops_portable_checkpoint.md" and flags & os.O_TRUNC:
                refused = True
                raise OSError("injected late IO failure, sandbox only")
            return original_open(path, flags, *args, **kwargs)

        with patch.object(os, "open", side_effect=intercept):
            with self.assertRaises(OSError):
                self._run((PATH, second))
        self.assertTrue(refused)
        self.assertEqual((self.root / PATH).read_bytes(), PAYLOAD)
        self.assertFalse((self.root / second).exists())

    def test_18_invalid_second_destination_preflight_blocks_first_write(self):
        (self.root / "scripts").mkdir()
        (self.root / "docs").mkdir(exist_ok=True)
        sentinel = self.outside / "target"
        sentinel.write_text("KEEP", encoding="utf-8")
        second = "docs/ops_portable_checkpoint.md"
        self.assertIn(second, json.loads(self.manifest.read_text())["producers"]["OPS"])
        (self.root / second).symlink_to(sentinel)
        with self.assertRaises(SystemExit):
            self._run((PATH, second))
        self.assertFalse((self.root / PATH).exists())
        self.assertEqual(sentinel.read_text(), "KEEP")


if __name__ == "__main__":
    unittest.main()
