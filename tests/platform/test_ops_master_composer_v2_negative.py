"""Sandbox-only regression matrix for the hardened MASTER #77 composer.

Loads the *exact* reviewed MASTER source with __main__ disabled, replaces git()
with in-memory output and ROOT with a TemporaryDirectory. No real Git commands,
network, PC4, Windows SCM, PostgreSQL or installed runtime are accessed.
"""
from __future__ import annotations

import importlib.util
import os
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

MASTER_SHA = "88ad7a7a82f35d1829b5eb07c0c1d4878741f527"
OPS_PIN = "0d4649f3a7cd3595ea58757f2abaf2f1b0558667"


class MasterV2SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sandbox = Path(self.temp.name).resolve()
        self.checkout = self.sandbox / "checkout"
        self.checkout.mkdir()
        self.outside = self.sandbox / "outside"
        self.outside.mkdir()

    def _module(self):
        path_text = os.environ.get("OPS_PINNED_MASTER_V2_COMPOSER_FILE")
        if not path_text:
            self.skipTest("Exact MASTER v2 source absent; pinned CI job owns this test")
        path = Path(path_text)
        self.assertTrue(path.is_file())
        self.assertFalse(path.is_symlink())
        spec = importlib.util.spec_from_file_location("ops_master_v2_sandbox_only", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(dict(module.PRODUCERS)["OPS"], OPS_PIN)
        module.ROOT = self.checkout
        return module

    def _run(self, module, filename="scripts/safe_fixture.py", *, mode="100644",
             ci="true", actions="true", workspace=None, contents=b"TEST_ONLY",
             before_write=None):
        calls = []
        def git_stub(*args):
            calls.append(args)
            if args[0] == "rev-parse":
                return (MASTER_SHA if args[1] == "HEAD" else args[1].split("^")[0]).encode()
            if args[0] == "diff":
                return (("A\t" + filename + "\n").encode() if args[-1] == OPS_PIN else b"")
            if args[0] == "ls-tree":
                return f"{mode} blob {'0' * 40}\t{filename}\n".encode()
            if args[0] == "show":
                return contents
            raise AssertionError(f"Unexpected real git operation: {args[0]}")

        env = {
            "CI": ci,
            "GITHUB_ACTIONS": actions,
            "GITHUB_WORKSPACE": str(workspace or self.checkout),
            "EXPECTED_STAGING_SHA": MASTER_SHA,
        }
        def execute():
            with patch.object(module, "git", side_effect=git_stub):
                with patch.dict(os.environ, env, clear=True):
                    with redirect_stdout(StringIO()) as output:
                        module.main()
                        return output.getvalue()
        if before_write is None:
            result = execute()
        else:
            # Controlled single-threaded substitution between preflight and
            # final write, *only inside* self.sandbox.
            original = Path.write_bytes
            swapped = False
            def intercept(path, data):
                nonlocal swapped
                if not swapped and path.name == "race_fixture.py":
                    swapped = True
                    before_write(path)
                return original(path, data)
            with patch.object(Path, "write_bytes", intercept):
                result = execute()
            self.assertTrue(swapped)
        return result, calls

    def test_regular_file_smoke_and_no_native_launcher_invocation(self):
        module = self._module()
        output, calls = self._run(module)
        self.assertIn("COMPOSITION_ONLY: not a commit", output)
        self.assertEqual((self.checkout / "scripts/safe_fixture.py").read_bytes(), b"TEST_ONLY")
        self.assertTrue(any(c[0] == "ls-tree" for c in calls))
        self.assertFalse(any(c[0] in {"sc", "powershell", "start", "reboot"} for c in calls))

    def test_new_guard_refuses_parent_traversal_and_protected_alias(self):
        module = self._module()
        for candidate in ("scripts/../../outside/escaped.py",
                          "scripts/../src/api/worker.py",
                          "scripts/../../outside/installer_hook.py"):
            with self.subTest(candidate=candidate), self.assertRaises(SystemExit):
                self._run(module, candidate)
        self.assertFalse((self.outside / "escaped.py").exists())
        self.assertFalse((self.checkout / "src/api/worker.py").exists())

    def test_new_guard_refuses_destination_parent_and_file_symlinks(self):
        module = self._module()
        try:
            (self.checkout / "docs").symlink_to(self.outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation unavailable on hosted runner")
        with self.assertRaises(SystemExit):
            self._run(module, "docs/escape.txt")
        self.assertFalse((self.outside / "escape.txt").exists())
        (self.checkout / "scripts").mkdir()
        target = self.outside / "sentinel.py"
        target.write_text("KEEP")
        (self.checkout / "scripts" / "link.py").symlink_to(target)
        with self.assertRaises(SystemExit):
            self._run(module, "scripts/link.py")
        self.assertEqual(target.read_text(), "KEEP")

    def test_new_guard_refuses_git_symlinks_gitlinks_and_special_modes(self):
        module = self._module()
        for mode in ("120000", "160000", "040000", "100600"):
            with self.subTest(mode=mode), self.assertRaises(SystemExit):
                self._run(module, "scripts/mode_fixture.py", mode=mode)
        self.assertFalse((self.checkout / "scripts/mode_fixture.py").exists())

    def test_missing_ci_or_wrong_workspace_rejected_but_forged_flags_remain(self):
        module = self._module()
        for kwargs in (
            {"ci": "false"},
            {"actions": "false"},
            {"workspace": self.outside},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(SystemExit):
                self._run(module, "scripts/no_ci_fixture.py", **kwargs)
        self.assertFalse((self.checkout / "scripts/no_ci_fixture.py").exists())
        # There is still no non-forgeable independent runner identity.
        # A local caller with matching environment strings can pass.
        output, _ = self._run(module, "scripts/forged_fixture.py")
        self.assertIn("COMPOSITION_ONLY", output)
        self.assertEqual((self.checkout / "scripts/forged_fixture.py").read_bytes(), b"TEST_ONLY")

    def test_unapproved_path_still_allowed_by_folder_prefix(self):
        module = self._module()
        output, _ = self._run(module, "scripts/unreviewed_installer_hook.py")
        self.assertIn("COMPOSITION_ONLY", output)
        self.assertTrue((self.checkout / "scripts/unreviewed_installer_hook.py").exists())

    def test_new_preflight_still_races_a_symlink_swap_before_write(self):
        module = self._module()
        directory = self.checkout / "scripts"
        directory.mkdir()
        sentinel = self.outside / "outside_race.py"
        sentinel.write_text("KEEP")
        def swap(path):
            self.assertEqual(path.name, "race_fixture.py")
            self.assertFalse(path.exists())
            path.symlink_to(sentinel)
        self._run(module, "scripts/race_fixture.py", before_write=swap)
        self.assertEqual(sentinel.read_bytes(), b"TEST_ONLY",
                         "A post-preflight symlink swap is still followed by write_bytes")

    def test_no_service_activation_calls_in_actual_master_ast(self):
        module = self._module()
        import ast
        source = Path(os.environ["OPS_PINNED_MASTER_V2_COMPOSER_FILE"]).read_text()
        tree = ast.parse(source)
        calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    calls.append(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    calls.append(node.func.attr)
        self.assertFalse(set(calls) & {
            "Popen", "system", "kill", "terminate", "StartService",
            "CreateProcess", "ShellExecute", "reboot", "runpy",
        })
        self.assertNotIn("ops_native_fixture_launcher", source)


if __name__ == "__main__":
    unittest.main()
