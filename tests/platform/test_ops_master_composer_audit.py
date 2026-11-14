"""Read-only negative checks for MASTER disposable source composition scope."""
from __future__ import annotations

import os
from pathlib import Path
import unittest

from scripts.ops_master_composer_audit import (
    EXPECTED_BASE,
    audit_master_composer,
)
from scripts.ops_native_optin_handoff import (
    CI_AND_DOC_FILES,
    RUNTIME_FILES,
    TEST_FILES,
)

OPS_PIN = "0d4649f3a7cd3595ea58757f2abaf2f1b0558667"
BASE = EXPECTED_BASE
APPROVED = sorted(set((*RUNTIME_FILES, *TEST_FILES, *CI_AND_DOC_FILES)))


def fixture():
    return (
        f'BASE = "{BASE}"\n'
        'PRODUCERS = (("DATA", "d5bfdad445a1eefb9dc12bf2725a00f171e2c321"), '
        f'("OPS", "{OPS_PIN}"))\n'
        'PROTECTED = ("src/integration/pc4_runtime.py", '
        '"src/api/worker.py", "pyproject.toml")\n'
        "def main():\n"
        "    if CI != 'true':\n"
        "        raise SystemExit('CI-only')\n"
        "    if EXPECTED_STAGING_SHA is None:\n"
        "        raise SystemExit('staging SHA changed')\n"
        "    git('diff', '--name-status', BASE, OPS_SHA)\n"
    )


class MasterComposerScopeAuditTests(unittest.TestCase):
    def test_exact_whitelist_is_not_inferred_from_successful_ast(self):
        source = fixture()
        report = audit_master_composer(
            source, changed_ops_paths=[*APPROVED, "scripts/ops_disposable_api.py"],
            expected_ops_sha=OPS_PIN,
        )
        self.assertEqual(report["result"], "BLOCKED_SCOPE_TOO_BROAD")
        self.assertEqual(report["extra_paths"], ["scripts/ops_disposable_api.py"])
        self.assertEqual(report["extra_count"], 1)
        self.assertTrue(report["copies_all_ops_diff"])
        self.assertFalse(report["minimal_optin_copy_confirmed"])
        self.assertTrue(report["ci_and_staging_sha_guards_present"])
        self.assertFalse(report["native_installed_restart_authorized"])
        self.assertFalse(report["direct_native_activation_calls_in_composer"])

    def test_unbounded_composer_still_requires_explicit_scope_when_no_extras(self):
        report = audit_master_composer(
            fixture(), changed_ops_paths=APPROVED, expected_ops_sha=OPS_PIN
        )
        self.assertEqual(report["extra_count"], 0)
        self.assertEqual(report["result"], "BLOCKED_SCOPE_TOO_BROAD")
        self.assertFalse(report["minimal_optin_copy_confirmed"])

    def test_missing_dependency_or_foreign_sha_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "approved_ops_handoff_files_missing"):
            audit_master_composer(
                fixture(), changed_ops_paths=[p for p in APPROVED
                                              if p != "scripts/ops_pg_claim.py"],
                expected_ops_sha=OPS_PIN,
            )
        with self.assertRaisesRegex(ValueError, "sha_pin_mismatch"):
            audit_master_composer(
                fixture(), changed_ops_paths=APPROVED, expected_ops_sha="b" * 40
            )
        with self.assertRaisesRegex(ValueError, "invalid_ops_pr_changed_file_inventory"):
            audit_master_composer(
                fixture(), changed_ops_paths=[*APPROVED, APPROVED[0]],
                expected_ops_sha=OPS_PIN,
            )

    def test_unguarded_installed_runtime_or_process_spawn_refused(self):
        for modification in (
            'PROTECTED = ("src/integration/pc4_runtime.py",\n)',
            'PROTECTED = ("src/api/worker.py",)',
        ):
            changed = fixture().replace(
                'PROTECTED = ("src/integration/pc4_runtime.py", '
                '"src/api/worker.py", "pyproject.toml")',
                modification,
            )
            with self.subTest(modification=modification), self.assertRaises(
                (ValueError, SyntaxError)
            ):
                audit_master_composer(
                    changed, changed_ops_paths=APPROVED, expected_ops_sha=OPS_PIN
                )
        malicious = fixture() + "\ndef activate():\n    subprocess.Popen(['native.exe'])\n"
        with self.assertRaisesRegex(ValueError, "activation_call"):
            audit_master_composer(
                malicious, changed_ops_paths=APPROVED, expected_ops_sha=OPS_PIN
            )

    @unittest.skipUnless(
        os.environ.get("OPS_PINNED_MASTER_COMPOSER_FILE")
        and os.environ.get("OPS_PINNED_CHANGED_PATHS_FILE"),
        "NOT_RUN: pinned MASTER and OPS Git checkout not materialized",
    )
    def test_actual_master_script_copies_59_not_minimal_ops_files(self):
        actual = Path(os.environ["OPS_PINNED_MASTER_COMPOSER_FILE"])
        changed = Path(os.environ["OPS_PINNED_CHANGED_PATHS_FILE"])
        self.assertFalse(actual.is_symlink())
        self.assertFalse(changed.is_symlink())
        source = actual.read_text(encoding="utf-8")
        paths = changed.read_text(encoding="utf-8").splitlines()
        report = audit_master_composer(
            source, changed_ops_paths=paths, expected_ops_sha=OPS_PIN
        )
        self.assertEqual(len(paths), 59)
        self.assertEqual(report["approved_count"], 31)
        self.assertEqual(report["actually_composed_ops_count"], 59)
        self.assertEqual(report["extra_count"], 30)
        self.assertEqual(report["result"], "BLOCKED_SCOPE_TOO_BROAD")
        self.assertTrue(report["copies_all_ops_diff"])
        self.assertIn("scripts/ops_disposable_api.py", report["extra_paths"])
        self.assertIn("tests/platform/test_ops_pg_load_real.py", report["extra_paths"])
        self.assertNotIn("scripts/ops_pg_claim.py", report["extra_paths"])
        self.assertEqual(report["approved_missing"], [
            "scripts/ops_master_composer_audit.py",
            "tests/platform/test_ops_master_composer_audit.py",
        ])
        self.assertTrue(report["protected_installed_runtime_paths_guarded"])
        self.assertFalse(report["direct_native_activation_calls_in_composer"])


if __name__ == "__main__":
    unittest.main()
