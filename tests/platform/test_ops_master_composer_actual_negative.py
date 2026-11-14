"""Safe sandbox reproduction of MASTER #77 composer's negative security cases.

The pinned MASTER module is loaded by path with its __main__ guard dormant,
then its git() and ROOT are replaced. Every write stays inside TemporaryDirectory.
This NEVER contacts GitHub, PostgreSQL, PC4, SCM, or the installed tester.
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

from scripts.ops_master_composer_hardening import (
    MasterComposeRefused,
    audit_composer_runtime_surface,
    require_ci_runner_identity,
    require_repo_file_path,
    require_safe_destination,
)

MASTER_SHA = "4e64ec9f6a1da453019b68e598e235036701739f"
OPS_PIN = "0d4649f3a7cd3595ea58757f2abaf2f1b0558667"


class MasterComposerActualNegativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sandbox = Path(self.tmp.name).resolve()
        self.checkout = self.sandbox / "checkout"
        self.checkout.mkdir()
        (self.checkout / "scripts").mkdir()
        (self.checkout / "src" / "api").mkdir(parents=True)
        self.outside = self.sandbox / "outside"
        self.outside.mkdir()

    def _actual(self):
        source_file = os.environ.get("OPS_PINNED_MASTER_COMPOSER_FILE")
        if not source_file:
            self.skipTest("NOT_RUN: exact reviewed MASTER script not checked out")
        path = Path(source_file)
        self.assertTrue(path.is_file())
        self.assertFalse(path.is_symlink())
        spec = importlib.util.spec_from_file_location(
            "ops_master_disposable_test_no_main", path
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.BASE, "3b2eef8a4139195db1a775545e83f1c1f58bf403")
        self.assertEqual(dict(module.PRODUCERS)["OPS"], OPS_PIN)
        module.ROOT = self.checkout
        return module

    def _reproduce(self, module, filename, *, contents=b"SANDBOX_ONLY"):
        calls = []

        def fake_git(*arguments):
            calls.append(arguments)
            operation = arguments[0]
            if operation == "rev-parse":
                revision = arguments[1]
                return (MASTER_SHA if revision == "HEAD" else revision.split("^")[0]).encode()
            if operation == "diff":
                return (("M\t" + filename + "\n").encode()
                        if arguments[-1] == OPS_PIN else b"")
            if operation == "show":
                return contents
            raise AssertionError("Unmocked Git process invocation")

        # Only CI=true and the staging SHA are set, no GitHub host identity.
        with patch.object(module, "git", side_effect=fake_git):
            with patch.dict(os.environ, {
                "CI": "true", "EXPECTED_STAGING_SHA": MASTER_SHA
            }, clear=True):
                with redirect_stdout(StringIO()):
                    module.main()
        self.assertTrue(calls)
        self.assertEqual(calls[0], ("rev-parse", "HEAD"))

    def test_actual_master_spoofed_ci_true_allows_untrusted_workspace_write(self):
        module = self._actual()
        target = self.checkout / "scripts" / "ops_untrusted.py"
        self._reproduce(module, "scripts/ops_untrusted.py")
        self.assertEqual(target.read_bytes(), b"SANDBOX_ONLY")
        # The hardened, separate proposal denies the same two-value env.
        with self.assertRaisesRegex(MasterComposeRefused, "missing_hosted_ci_identity"):
            require_ci_runner_identity(
                {"CI": "true", "EXPECTED_STAGING_SHA": MASTER_SHA},
                expected_staging_sha=MASTER_SHA,
            )

    def test_actual_master_parent_traversal_can_write_outside_checkout(self):
        module = self._actual()
        target = self.outside / "escaped.py"
        self._reproduce(module, "scripts/../../outside/escaped.py")
        self.assertEqual(target.read_bytes(), b"SANDBOX_ONLY")
        with self.assertRaises(MasterComposeRefused):
            require_repo_file_path("scripts/../../outside/escaped.py")

    def test_actual_master_symlink_parent_can_write_outside_checkout(self):
        module = self._actual()
        (self.checkout / "docs").symlink_to(self.outside, target_is_directory=True)
        target = self.outside / "owned.txt"
        self._reproduce(module, "docs/owned.txt")
        self.assertEqual(target.read_bytes(), b"SANDBOX_ONLY")
        with self.assertRaisesRegex(MasterComposeRefused, "symlinked_destination_parent"):
            require_safe_destination(self.checkout, "docs/owned.txt")

    def test_actual_master_existing_file_symlink_is_followed(self):
        module = self._actual()
        target = self.outside / "data.py"
        target.write_text("KEEP")
        (self.checkout / "scripts" / "ops_link.py").symlink_to(target)
        self._reproduce(module, "scripts/ops_link.py")
        self.assertEqual(target.read_bytes(), b"SANDBOX_ONLY")
        with self.assertRaisesRegex(MasterComposeRefused, "symlinked_destination_file"):
            require_safe_destination(self.checkout, "scripts/ops_link.py")

    def test_actual_master_alias_can_overwrite_protected_worker(self):
        module = self._actual()
        target = self.checkout / "src" / "api" / "worker.py"
        target.write_text("IMMUTABLE_PC4")
        self._reproduce(module, "scripts/../src/api/worker.py")
        self.assertEqual(target.read_bytes(), b"SANDBOX_ONLY")
        with self.assertRaisesRegex(MasterComposeRefused, "path_traversal"):
            require_repo_file_path("scripts/../src/api/worker.py")

    def test_actual_master_accepts_unapproved_scripts_filename(self):
        module = self._actual()
        filename = "scripts/installer_hook.py"
        self._reproduce(module, filename)
        self.assertEqual((self.checkout / filename).read_bytes(), b"SANDBOX_ONLY")
        # A broad prefix check is not the approved 31-file OPS allowlist.
        self.assertFalse(audit_composer_runtime_surface(
            Path(os.environ["OPS_PINNED_MASTER_COMPOSER_FILE"]).read_text()
        )["native_launcher_activation_call_found"])

    def test_actual_master_has_no_native_launch_but_is_not_hardened(self):
        path = os.environ.get("OPS_PINNED_MASTER_COMPOSER_FILE")
        if not path:
            self.skipTest("NOT_RUN: exact MASTER script not available")
        report = audit_composer_runtime_surface(Path(path).read_text())
        self.assertFalse(report["runtime_runner_binding_proven"])
        self.assertFalse(report["symlink_path_preflight_present"])
        self.assertFalse(report["atomic_no_follow_open_present"])
        self.assertFalse(report["native_launcher_activation_call_found"])
        self.assertEqual(report["overall"], "BLOCKED_HARDENING_REQUIRED")


if __name__ == "__main__":
    unittest.main()
