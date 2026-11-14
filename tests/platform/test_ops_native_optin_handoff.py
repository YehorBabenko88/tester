"""OPS #73 explicit review-only #77 handoff, no installer or runtime mutation."""
import copy
import hashlib
import json
from pathlib import Path
import unittest

from scripts.ops_candidate_compatibility import SCHEMA
from scripts.ops_native_optin_handoff import (
    HANDOFF_SCHEMA,
    PINNED_PRODUCERS,
    PREPARE_ONLY,
    prepare_native_source_handoff,
    require_live_native_activation,
)


def sample_report():
    source_hashes = {
        role: hashlib.sha256(role.encode()).hexdigest()
        for role in ("pc4", "api", "worker", "data", "research")
    }
    body = {
        "schema": SCHEMA,
        "source_digests": source_hashes,
        "ui_api_sha256": hashlib.sha256(b"ui-api").hexdigest(),
    }
    fingerprint = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        **body,
        "manifest_sha256": fingerprint,
        "installed_native_activation": "BLOCKED",
        "default_activation_enabled": False,
        "pc4_restart_authorized": False,
        "pg5432_access": False,
        "blockers": ["installed_api_worker_generation_ack_not_tested"],
    }


class OptinHandoffTests(unittest.TestCase):
    def test_optin_only_generates_review_plan_and_never_activation(self):
        plan = prepare_native_source_handoff(
            sample_report(), producer_heads=PINNED_PRODUCERS,
            opt_in=PREPARE_ONLY,
        )
        self.assertEqual(plan["schema"], HANDOFF_SCHEMA)
        self.assertEqual(plan["installed_native_activation"], "BLOCKED")
        self.assertFalse(plan["automatic_activation"])
        self.assertFalse(plan["database_access_performed"])
        self.assertFalse(plan["process_spawn_performed"])
        self.assertFalse(plan["physical_pc4_action_performed"])
        self.assertFalse(plan["ready_to_merge_or_install"])
        self.assertEqual(len(plan["plan_sha256"]), 64)
        self.assertIn("scripts/ops_native_scoped_stop.py", plan["runtime_files"])
        self.assertIn("scripts/ops_native_optin_handoff.py", plan["runtime_files"])
        self.assertNotIn("src/api/worker.py", plan["runtime_files"])
        self.assertNotIn("src/api/main.py", plan["runtime_files"])
        self.assertNotIn("src/integration/pc4_runtime.py", plan["runtime_files"])
        with self.assertRaises(PermissionError):
            require_live_native_activation(plan)

    def test_no_optin_or_enable_attempt_refused_before_any_work(self):
        for action in (None, "", "ENABLE", "INSTALL", "RESTART_PC4"):
            with self.subTest(action=action), self.assertRaises(PermissionError):
                prepare_native_source_handoff(
                    sample_report(), producer_heads=PINNED_PRODUCERS,
                    opt_in=action,
                )

    def test_source_manifest_and_exact_pins_cannot_be_forged(self):
        good = sample_report()
        altered = copy.deepcopy(good)
        altered["source_digests"]["worker"] = "a" * 64
        with self.assertRaisesRegex(ValueError, "source_manifest_tampered"):
            prepare_native_source_handoff(
                altered, producer_heads=PINNED_PRODUCERS,
                opt_in=PREPARE_ONLY,
            )
        bad_pins = dict(PINNED_PRODUCERS, data="b" * 40)
        with self.assertRaisesRegex(ValueError, "staging_77_producer_pin_mismatch"):
            prepare_native_source_handoff(
                good, producer_heads=bad_pins, opt_in=PREPARE_ONLY,
            )

    def test_native_autoenable_flags_and_bad_hashes_refused(self):
        for field, value in (
            ("installed_native_activation", "ENABLED"),
            ("default_activation_enabled", True),
            ("pc4_restart_authorized", True),
            ("pg5432_access", True),
        ):
            altered = sample_report()
            altered[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                ValueError, "candidate_attempts_unsafe_native_activation"
            ):
                prepare_native_source_handoff(
                    altered, producer_heads=PINNED_PRODUCERS,
                    opt_in=PREPARE_ONLY,
                )
        altered = sample_report()
        altered["ui_api_sha256"] = "not-a-hash"
        with self.assertRaisesRegex(ValueError, "unqualified_candidate_source_digest"):
            prepare_native_source_handoff(
                altered, producer_heads=PINNED_PRODUCERS,
                opt_in=PREPARE_ONLY,
            )

    def test_deterministic_file_inventory_and_no_installed_entrypoint_import(self):
        first = prepare_native_source_handoff(
            sample_report(), producer_heads=PINNED_PRODUCERS, opt_in=PREPARE_ONLY,
        )
        second = prepare_native_source_handoff(
            sample_report(), producer_heads=dict(reversed(list(PINNED_PRODUCERS.items()))),
            opt_in=PREPARE_ONLY,
        )
        self.assertEqual(first, second)
        self.assertEqual(
            first["plan_sha256"],
            hashlib.sha256(json.dumps(
                {key: value for key, value in first.items() if key != "plan_sha256"},
                sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest()
        )
        import ast
        root = Path(__file__).resolve().parents[2]
        # A static guard, not proof of OS runtime state: installed entrypoints
        # cannot import OPS launcher through ordinary Python import statements.
        for path in ("src/api/main.py", "src/api/worker.py",
                     "src/integration/pc4_runtime.py"):
            code = (root / path).read_text(encoding="utf-8")
            tree = ast.parse(code, filename=path)
            modules = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules.append(node.module)
            with self.subTest(path=path):
                self.assertFalse(any(
                    name.startswith(("scripts.ops_native_fixture_launcher",
                                     "scripts.ops_native_optin_handoff",
                                     "scripts.ops_native_scoped_stop"))
                    for name in modules
                ))
                self.assertNotIn("ops_native_fixture_launcher", code)


if __name__ == "__main__":
    unittest.main()
