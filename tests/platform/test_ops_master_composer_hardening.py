"""Negative security scenarios for MASTER composer hardening proposal.

Only a disposable temporary directory is used. MASTER is not invoked and
no Windows service, PC4 process, PostgreSQL or production path is touched.
"""
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.ops_master_composer_hardening import (
    MasterComposeRefused,
    audit_composer_runtime_surface,
    require_ci_runner_identity,
    require_repo_file_path,
    require_safe_destination,
)

SHA = "4e64ec9f6a1da453019b68e598e235036701739f"


class MasterComposerHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.runner_temp = self.root / "runner-temp"
        self.runner_temp.mkdir()
        self.environment = {
            "CI": "true",
            "GITHUB_ACTIONS": "true",
            "GITHUB_WORKSPACE": str(self.workspace),
            "RUNNER_TEMP": str(self.runner_temp),
            "GITHUB_SHA": SHA,
            "GITHUB_REPOSITORY": "YehorBabenko88/tester",
            "GITHUB_RUN_ID": "1234",
            "GITHUB_JOB": "disposable-compose",
            "RUNNER_OS": "Linux",
        }

    def test_forged_ci_true_alone_refused(self):
        with self.assertRaisesRegex(MasterComposeRefused, "missing_hosted_ci_identity"):
            require_ci_runner_identity({"CI": "true"}, expected_staging_sha=SHA)
        require_ci_runner_identity(self.environment, expected_staging_sha=SHA)
        for key, fake in (
            ("GITHUB_ACTIONS", "false"),
            ("GITHUB_SHA", "1" * 40),
            ("GITHUB_REPOSITORY", "untrusted/repo"),
            ("GITHUB_RUN_ID", "attacker"),
            ("RUNNER_OS", "MacOS"),
        ):
            bad = {**self.environment, key: fake}
            with self.subTest(key=key), self.assertRaises(MasterComposeRefused):
                require_ci_runner_identity(bad, expected_staging_sha=SHA)

    def test_paths_must_be_relative_regular_approved_and_protected(self):
        for value in (
            "../scripts/ops_test.py",
            "scripts/../src/api/worker.py",
            "src/api/worker.py",
            "src/integration/pc4_runtime.py",
            "pyproject.toml",
            "/etc/passwd",
            "C:/Windows/System32/drivers/etc/hosts",
            "scripts\\ops_test.py",
            "scripts//x.py",
            "scripts/./x.py",
            "scripts/%2e%2e/secret",
            ".github/../../x",
            "unknown/file.py",
            "tests/x.py/",
        ):
            # Encoded escapes need explicit decoding policy, tested separately:
            if "%2e" in value:
                continue
            with self.subTest(value=value), self.assertRaises(MasterComposeRefused):
                require_repo_file_path(value)
        self.assertEqual(
            str(require_repo_file_path("scripts/ops_master_composer_audit.py")),
            "scripts/ops_master_composer_audit.py",
        )

    def test_symlink_parent_and_file_refused_before_any_write(self):
        (self.workspace / "scripts").mkdir()
        (self.workspace / "outside").mkdir()
        symlink_parent = self.workspace / "docs"
        symlink_target = self.workspace / "outside"
        try:
            symlink_parent.symlink_to(symlink_target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable under current runner privileges")
        with self.assertRaisesRegex(MasterComposeRefused, "symlinked_destination_parent"):
            require_safe_destination(self.workspace, "docs/evil.txt")
        symlink_parent.unlink()
        original = self.workspace / "outside" / "original.py"
        original.write_text("KEEP ORIGINAL")
        link = self.workspace / "scripts" / "ops_target.py"
        link.symlink_to(original)
        with self.assertRaisesRegex(MasterComposeRefused, "symlinked_destination_file"):
            require_safe_destination(self.workspace, "scripts/ops_target.py")
        self.assertEqual(original.read_text(), "KEEP ORIGINAL")

    def test_non_directory_parent_and_checkout_escape_refused(self):
        bad_parent = self.workspace / "scripts"
        bad_parent.write_text("not a folder")
        with self.assertRaisesRegex(MasterComposeRefused, "non_directory"):
            require_safe_destination(self.workspace, "scripts/ops_target.py")
        bad_parent.unlink()
        with self.assertRaisesRegex(MasterComposeRefused, "untrusted_checkout_root"):
            require_safe_destination(self.workspace / "missing", "scripts/ops_target.py")
        assert require_safe_destination(self.workspace, "scripts/ops_target.py") == (
            self.workspace / "scripts" / "ops_target.py"
        )

    def test_master_current_source_fails_required_security_gates(self):
        pinned = os.getenv("OPS_PINNED_MASTER_COMPOSER_FILE")
        if not pinned:
            self.skipTest("exact MASTER source not materialized in pure test runner")
        source = Path(pinned).read_text(encoding="utf-8")
        report = audit_composer_runtime_surface(source)
        self.assertEqual(report["overall"], "BLOCKED_HARDENING_REQUIRED")
        self.assertFalse(report["runtime_runner_binding_proven"])
        self.assertFalse(report["symlink_path_preflight_present"])
        self.assertFalse(report["atomic_no_follow_open_present"])
        self.assertFalse(report["native_launcher_activation_call_found"])
        self.assertFalse(report["safe_to_run_outside_isolated_ci"])


if __name__ == "__main__":
    unittest.main()
