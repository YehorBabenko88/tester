"""Exact MASTER f3417969 security replay. All effects limited to TemporaryDirectory.

This uses the real composer source and manifest from pinned Git objects, but
replaces its git reader with deterministic synthetic *complete* producer diffs.
No network, installed PC4, SCM, PostgreSQL or native launcher is accessed.
"""
from __future__ import annotations

import ast
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

NEW_SHA = "f341796916768b4f6b0acb1bd7d7b69cbde55155"
PREV_SHA = "d6d6548fee441710e34f716d29adb4c25c7e4660"
OPS_PIN = "0d4649f3a7cd3595ea58757f2abaf2f1b0558667"
TARGET = "scripts/ops_native_scoped_stop.py"
SECOND = "docs/ops_portable_checkpoint.md"
SOURCE = b"COMPOSED_SANDBOX_ONLY"


class MasterV4ExactSandboxTests(unittest.TestCase):
    def setUp(self):
        src = os.getenv("OPS_MASTER_V4_COMPOSER_FILE")
        manifest_file = os.getenv("OPS_MASTER_V4_MANIFEST_FILE")
        if not src or not manifest_file:
            self.skipTest("NOT_RUN: exact committed MASTER v4 source not checked out")
        self.source = Path(src)
        self.committed = Path(manifest_file).read_bytes()
        self.approved = json.loads(self.committed)["producers"]
        self.assertEqual(tuple(self.approved), ("DATA", "RESEARCH", "UI", "OPS"))
        self.assertIn(TARGET, self.approved["OPS"])
        self.assertIn(SECOND, self.approved["OPS"])
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sandbox = Path(self.temp.name).resolve()
        self.root = self.sandbox / "checkout"
        self.root.mkdir()
        self.outside = self.sandbox / "outside"
        self.outside.mkdir()
        self.manifest = self.root / "docs/coordination/master-reviewed-producer-files.json"
        self.manifest.parent.mkdir(parents=True)
        self.manifest.write_bytes(self.committed)
        spec = importlib.util.spec_from_file_location("ops_master_v4_exact_sandbox", self.source)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.assertEqual(dict(self.module.PRODUCERS)["OPS"], OPS_PIN)
        self.module.ROOT = self.root

    def _run(self, *, env_changes=None, diff_override=None, mode_override=None,
             committed_override=None, bytes_override=None):
        calls = []
        def fake_git(*args):
            calls.append(args)
            if args[0] == "rev-parse":
                return (NEW_SHA if args[1] == "HEAD" else args[1].split("^{")[0]).encode()
            if args[0] == "show":
                if args[1] == "HEAD:docs/coordination/master-reviewed-producer-files.json":
                    return self.committed if committed_override is None else committed_override
                return SOURCE if bytes_override is None else bytes_override
            if args[0] == "diff":
                label = next(k for k, sha in self.module.PRODUCERS if sha == args[-1])
                entries = [( "A", path) for path in self.approved[label]]
                if diff_override and label in diff_override:
                    entries = diff_override[label]
                return "".join(f"{status}\t{path}\n" for status, path in entries).encode()
            if args[0] == "ls-tree":
                mode, kind = ("100644", "blob")
                if mode_override and args[-1] in mode_override:
                    mode, kind = mode_override[args[-1]]
                return f"{mode} {kind} {'f'*40}\t{args[-1]}\n".encode()
            raise AssertionError("unmocked real git call: " + repr(args))
        env = {
            "CI": "true", "GITHUB_ACTIONS": "true",
            "GITHUB_WORKSPACE": str(self.root), "GITHUB_SHA": NEW_SHA,
            "EXPECTED_STAGING_SHA": NEW_SHA,
            "GITHUB_REPOSITORY": "YehorBabenko88/tester",
            "GITHUB_RUN_ID": "12345", "RUNNER_OS": "Linux",
        }
        env.update(env_changes or {})
        with patch.object(self.module, "git", side_effect=fake_git):
            with patch.dict(os.environ, env, clear=True):
                with redirect_stdout(StringIO()) as out:
                    self.module.main()
                    return out.getvalue(), calls

    def test_01_exact_full_set_smoke_and_no_native_activation(self):
        with patch.object(subprocess, "Popen", side_effect=AssertionError("spawn forbidden")):
            with patch.object(subprocess, "run", side_effect=AssertionError("spawn forbidden")):
                with patch.object(os, "system", side_effect=AssertionError("shell forbidden")):
                    result, calls = self._run()
        self.assertIn("COMPOSITION_ONLY", result)
        self.assertEqual((self.root / TARGET).read_bytes(), SOURCE)
        self.assertEqual((self.root / SECOND).read_bytes(), SOURCE)
        self.assertTrue(any(c[0] == "ls-tree" for c in calls))
        tree = ast.parse(self.source.read_text(encoding="utf-8"))
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                called.add(fn.id if isinstance(fn, ast.Name) else
                           fn.attr if isinstance(fn, ast.Attribute) else "<call>")
        self.assertFalse(called & {
            "Popen", "run", "system", "start_service", "StartService", "CreateProcess",
            "ShellExecute", "reboot", "kill", "terminate", "runpy",
        })

    def test_02_hardlink_to_external_inode_NO_LONGER_TRUNCATED(self):
        outside = self.outside / "hardlink-target"
        outside.write_bytes(b"KEEP_ORIGINAL")
        (self.root / "scripts").mkdir()
        os.link(outside, self.root / TARGET)
        result, _ = self._run()
        self.assertIn("COMPOSITION_ONLY", result)
        self.assertEqual(outside.read_bytes(), b"KEEP_ORIGINAL")
        self.assertEqual((self.root / TARGET).read_bytes(), SOURCE)
        self.assertNotEqual(os.stat(outside).st_ino, os.stat(self.root / TARGET).st_ino)

    def test_03_hardlink_to_protected_worker_NO_LONGER_TRUNCATED(self):
        worker = self.root / "src/api/worker.py"
        worker.parent.mkdir(parents=True)
        worker.write_bytes(b"PROTECTED_PC4_BASE")
        (self.root / "scripts").mkdir()
        os.link(worker, self.root / TARGET)
        result, _ = self._run()
        self.assertIn("COMPOSITION_ONLY", result)
        self.assertEqual(worker.read_bytes(), b"PROTECTED_PC4_BASE")
        self.assertEqual((self.root / TARGET).read_bytes(), SOURCE)

    def test_04_modified_manifest_and_unlisted_file_refused(self):
        content = json.loads(self.committed)
        content["producers"]["OPS"].append("scripts/unreviewed_installer.py")
        self.manifest.write_text(json.dumps(content), encoding="utf-8")
        with self.assertRaises(SystemExit):
            self._run()
        self.manifest.write_bytes(self.committed)
        changes = {k: [("A", x) for x in v] for k,v in self.approved.items()}
        changes["OPS"].append(("A", "scripts/unreviewed_installer.py"))
        with self.assertRaises(SystemExit):
            self._run(diff_override=changes)
        self.assertFalse((self.root / "scripts/unreviewed_installer.py").exists())

    def test_05_symlinked_manifest_refused(self):
        external = self.outside / "manifest.json"
        external.write_bytes(self.committed)
        self.manifest.unlink()
        self.manifest.symlink_to(external)
        with self.assertRaises(SystemExit):
            self._run()
        self.assertEqual(external.read_bytes(), self.committed)

    def test_06_manifest_committed_head_mismatch_rejected(self):
        altered = self.committed.replace(b"master-reviewed-producer-files/1",
                                         b"master-reviewed-producer-files/2")
        with self.assertRaises(SystemExit):
            self._run(committed_override=altered)
        self.assertEqual(self.manifest.read_bytes(), self.committed)

    def test_07_missing_expected_file_or_deleted_status_refused(self):
        changes = {k: [("A", x) for x in v] for k,v in self.approved.items()}
        changes["OPS"] = [x for x in changes["OPS"] if x[1] != TARGET]
        with self.assertRaises(SystemExit):
            self._run(diff_override=changes)
        changes["OPS"].append(("D", TARGET))
        with self.assertRaises(SystemExit):
            self._run(diff_override=changes)
        self.assertFalse((self.root / TARGET).exists())

    def test_08_direct_protected_traversal_and_extra_refused(self):
        for bad in (
            "src/api/worker.py", "src/integration/pc4_runtime.py",
            "scripts/../src/api/worker.py", "scripts/../../outside/bad.py",
            "scripts/unapproved.py",
        ):
            changes = {k: [("A",x) for x in v] for k,v in self.approved.items()}
            changes["OPS"].append(("A", bad))
            with self.subTest(path=bad), self.assertRaises(SystemExit):
                self._run(diff_override=changes)
        self.assertFalse((self.outside / "bad.py").exists())

    def test_09_git_symlink_gitlink_nonregular_refused(self):
        for mode, kind in (("120000","blob"),("160000","commit"),
                           ("040000","tree"),("100600","blob")):
            with self.subTest(mode=mode), self.assertRaises(SystemExit):
                self._run(mode_override={TARGET:(mode,kind)})
        self.assertFalse((self.root / TARGET).exists())

    def test_10_parent_and_leaf_symlinks_refused(self):
        (self.root / "scripts").symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(SystemExit):
            self._run()
        (self.root / "scripts").unlink()
        (self.root / "scripts").mkdir()
        sentinel = self.outside / "sentinel"
        sentinel.write_bytes(b"KEEP")
        (self.root / TARGET).symlink_to(sentinel)
        with self.assertRaises(SystemExit):
            self._run()
        self.assertEqual(sentinel.read_bytes(), b"KEEP")

    def test_11_leaf_symlink_race_after_preflight_replaced_not_followed(self):
        (self.root / "scripts").mkdir()
        outside = self.outside / "leaf-race"
        outside.write_bytes(b"KEEP")
        original_replace = os.replace
        raced = False
        def intercept(src, dst, *args, **kwargs):
            nonlocal raced
            if not raced and dst == Path(TARGET).name:
                raced = True
                (self.root / TARGET).symlink_to(outside)
            return original_replace(src, dst, *args, **kwargs)
        with patch.object(os, "replace", side_effect=intercept):
            self._run()
        self.assertTrue(raced)
        self.assertEqual(outside.read_bytes(), b"KEEP")
        self.assertEqual((self.root / TARGET).read_bytes(), SOURCE)

    def test_12_parent_symlink_swap_before_fd_open_refused(self):
        (self.root / "scripts").mkdir()
        orig = os.open
        swapped = False
        def intercept(path, flags, *args, **kwargs):
            nonlocal swapped
            if not swapped and path == "scripts" and "dir_fd" in kwargs:
                swapped = True
                (self.root / "scripts").rename(self.outside / "moved-before-open")
                (self.root / "scripts").symlink_to(self.outside, target_is_directory=True)
            return orig(path, flags, *args, **kwargs)
        with patch.object(os,"open",side_effect=intercept):
            with self.assertRaises(OSError):
                self._run()
        self.assertTrue(swapped)
        self.assertFalse((self.outside / Path(TARGET).name).exists())

    def test_13_relocated_open_parent_directory_STILL_ESCAPE(self):
        (self.root / "scripts").mkdir()
        relocated = self.outside / "relocated-scripts"
        old_replace = os.replace
        raced = False
        def intercept(src,dst,*args,**kwargs):
            nonlocal raced
            if not raced and dst == Path(TARGET).name:
                raced = True
                (self.root / "scripts").rename(relocated)
                (self.root / "scripts").mkdir()
            return old_replace(src,dst,*args,**kwargs)
        with patch.object(os, "replace", side_effect=intercept):
            out,_ = self._run()
        self.assertTrue(raced)
        self.assertIn("COMPOSITION_ONLY",out)
        self.assertEqual((relocated / Path(TARGET).name).read_bytes(), SOURCE)
        self.assertFalse((self.root / TARGET).exists())

    def test_14_later_replace_failure_STILL_PARTIAL_COMPOSITION(self):
        old_replace = os.replace
        failed = False
        def intercept(src,dst,*args,**kwargs):
            nonlocal failed
            if dst == Path(TARGET).name:
                failed = True
                raise OSError("injected late destination replace error")
            return old_replace(src,dst,*args,**kwargs)
        with patch.object(os, "replace", side_effect=intercept):
            with self.assertRaises(OSError):
                self._run()
        self.assertTrue(failed)
        # DATA begins before OPS, so prior approved source files were written.
        self.assertTrue((self.root/"src/ingest/execution_contract.py").exists())
        self.assertFalse((self.root/TARGET).exists())

    def test_15_ci_flag_sha_and_runner_metadata_mismatch_refused(self):
        for name,bad in (
            ("CI","false"),("GITHUB_ACTIONS","false"),
            ("GITHUB_SHA","0"*40),("EXPECTED_STAGING_SHA","f"*40),
            ("GITHUB_REPOSITORY","other/repo"),("GITHUB_RUN_ID","invalid"),
            ("RUNNER_OS","Windows"),("GITHUB_WORKSPACE",str(self.outside)),
        ):
            with self.subTest(name=name), self.assertRaises(SystemExit):
                self._run(env_changes={name:bad})
        self.assertFalse((self.root/TARGET).exists())

    def test_16_matching_forged_environment_STILL_ACCEPTED(self):
        result,_ = self._run()
        self.assertIn("COMPOSITION_ONLY",result)
        # Any local attacker able to forge Git and the checked-out repo can
        # supply matching metadata; this is NOT runner attestation.

    def test_17_manifest_producer_diff_equality_but_duplicate_paths_accepted(self):
        changes = {k: [("A",x) for x in v] for k,v in self.approved.items()}
        changes["OPS"].append(("A", TARGET))
        output,_ = self._run(diff_override=changes)
        self.assertIn("COMPOSITION_ONLY",output)
        # The set conversion ignores duplicate entries in git diff.

    def test_18_unexpected_preexisting_workspace_file_NOT_REJECTED(self):
        unexpected = self.root/"scripts/unapproved_preexisting.py"
        unexpected.parent.mkdir()
        unexpected.write_bytes(b"UNKNOWN")
        result,_ = self._run()
        self.assertIn("COMPOSITION_ONLY",result)
        self.assertEqual(unexpected.read_bytes(),b"UNKNOWN")

    def test_20_staged_temp_inode_replaced_by_symlink_STILL_RACE(self):
        # Private temporary filenames are random, but an adversary with
        # write access to this directory can race after the writer closes it.
        # Replacing the temporary source with a symlink plants a symlink in
        # the finished candidate. No actual outside file is modified.
        sentinel = self.outside / "staged-symlink-target"
        sentinel.write_bytes(b"KEEP")
        original = os.replace
        swapped = False
        def intercept(src,dst,*args,**kwargs):
            nonlocal swapped
            if not swapped and dst == Path(TARGET).name:
                swapped = True
                src_fd = kwargs["src_dir_fd"]
                os.unlink(src,dir_fd=src_fd)
                os.symlink(sentinel,src,dir_fd=src_fd)
            return original(src,dst,*args,**kwargs)
        with patch.object(os,"replace",side_effect=intercept):
            output,_=self._run()
        self.assertTrue(swapped)
        self.assertIn("COMPOSITION_ONLY",output)
        self.assertTrue((self.root/TARGET).is_symlink())
        self.assertEqual((self.root/TARGET).resolve(), sentinel)
        self.assertEqual(sentinel.read_bytes(),b"KEEP")

    def test_21_forged_git_blob_content_under_exact_head_STILL_TRUSTS_GIT(self):
        # Mocked Git proves the boundary: a compromised Git command can
        # supply bytes unrelated to the displayed ls-tree object SHA.
        # This is NOT a bypass of genuine Git's object integrity.
        hostile=b"UNVERIFIED_GIT_OUTPUT_ONLY"
        output,_=self._run(bytes_override=hostile)
        self.assertIn("COMPOSITION_ONLY",output)
        self.assertEqual((self.root/TARGET).read_bytes(),hostile)

    def test_19_second_target_symlink_preflight_refuses_before_any_write(self):
        sentinel = self.outside/"sentinel"
        sentinel.write_bytes(b"KEEP")
        target = self.root/SECOND
        target.parent.mkdir(parents=True,exist_ok=True)
        target.symlink_to(sentinel)
        with self.assertRaises(SystemExit):
            self._run()
        self.assertFalse((self.root/"src/ingest/execution_contract.py").exists())
        self.assertEqual(sentinel.read_bytes(),b"KEEP")


if __name__=="__main__":
    unittest.main()
