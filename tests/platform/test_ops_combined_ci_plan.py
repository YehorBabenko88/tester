"""Pure regressions for exact-SHA, non-executing combined CI plan."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import unittest

from scripts.ops_candidate_compatibility import SCHEMA
from scripts.ops_combined_ci_plan import (
    PLAN_SCHEMA,
    combined_acceptance_plan,
)
from scripts.ops_native_optin_handoff import (
    HANDOFF_SCHEMA,
    PINNED_PRODUCERS,
    PREPARE_ONLY,
    prepare_native_source_handoff,
)

COMBINED = "1" * 40
TREE = "2" * 40
OPS = "3" * 40
PRODUCERS = {**PINNED_PRODUCERS, "ops": OPS}


def reviewed():
    sources = {
        k: hashlib.sha256(k.encode()).hexdigest()
        for k in ("pc4", "api", "worker", "data", "research")
    }
    fields = {
        "schema": SCHEMA,
        "source_digests": sources,
        "ui_api_sha256": hashlib.sha256(b"ui").hexdigest(),
    }
    fields["manifest_sha256"] = hashlib.sha256(
        json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    fields.update(installed_native_activation="BLOCKED",
                  default_activation_enabled=False, pc4_restart_authorized=False,
                  pg5432_access=False, blockers=[
                      "persistent_external_original_handle_guardian_not_implemented",
                  ])
    handoff = prepare_native_source_handoff(
        fields, producer_heads=PINNED_PRODUCERS, opt_in=PREPARE_ONLY,
    )
    assert handoff["schema"] == HANDOFF_SCHEMA
    return handoff


def create(**updates):
    args = {
        "combined_sha": COMBINED,
        "ops_sha": OPS,
        "source_tree_sha": TREE,
        "producer_shas": PRODUCERS,
        "handoff": reviewed(),
        "ephemeral_ci_only": True,
    }
    args.update(updates)
    return combined_acceptance_plan(**args)


class CombinedAcceptancePlanTests(unittest.TestCase):
    def test_only_nonexecuting_disposable_exact_head_plan(self):
        plan = create()
        self.assertEqual(plan["schema"], PLAN_SCHEMA)
        self.assertEqual(plan["combined_head_sha"], COMBINED)
        self.assertEqual(plan["combined_tree_sha"], TREE)
        self.assertFalse(plan["physical_pc4_access"])
        self.assertFalse(plan["production_pg5432_access"])
        self.assertFalse(plan["automatic_native_start"])
        self.assertFalse(plan["staging_release_authorized"])
        self.assertFalse(plan["commands_executed_by_this_module"])
        self.assertEqual(plan["execution"], "NOT_RUN")
        self.assertEqual(plan["installed_native_activation"], "BLOCKED")
        self.assertEqual(len(plan["plan_sha256"]), 64)
        self.assertIn(
            f'test "$(git rev-parse HEAD)" = "{COMBINED}"',
            plan["source_checkout_verification"],
        )
        self.assertIn(
            f'test "$(git rev-parse HEAD^{{tree}})" = "{TREE}"',
            plan["source_checkout_verification"],
        )
        stages = {s["id"]: s for s in plan["stages"]}
        for name in (
            "source_and_security", "platform_security_postgres16",
            "postgres14_storage_migration_and_http",
            "postgres18_storage_migration_and_http",
            "postgres16_real_source_readback_research",
            "postgres16_ops_lease_wal_and_resource",
            "node_chromium_ui_13tf",
            "fastapi_genuine_synthetic_f_g_d_browser",
            "win32_native_original_handle_and_rollback",
            "windows_installer_isolation",
        ):
            self.assertIn(name, stages)
        ports = [
            x["postgres"]["published_port"]
            for x in plan["stages"] if "postgres" in x
        ]
        self.assertNotIn(5432, ports)
        self.assertTrue(all(55400 <= p <= 55499 for p in ports))

    def test_invalid_or_unreviewed_inputs_refuse_without_any_execution(self):
        bad_cases = [
            {"ephemeral_ci_only": False},
            {"combined_sha": OPS},
            {"combined_sha": "main"},
            {"combined_sha": "1" * 39},
            {"combined_sha": "3" * 40},
            {"source_tree_sha": "not-a-tree"},
            {"ops_sha": "invalid"},
            {"producer_shas": {**PRODUCERS, "ops": "4" * 40}},
            {"producer_shas": {"data": "a" * 40}},
            {"producer_shas": {**PRODUCERS, "data": "4" * 40}},
        ]
        for case in bad_cases:
            with self.subTest(case=case), self.assertRaises(
                (ValueError, PermissionError)
            ):
                create(**case)

    def test_manifest_tampering_and_install_flags_are_refused(self):
        handoff = reviewed()
        for field, value in (
            ("automatic_activation", True),
            ("installed_native_activation", "ENABLED"),
            ("ready_to_merge_or_install", True),
            ("requested_action", "INSTALL"),
        ):
            mutated = copy.deepcopy(handoff)
            mutated[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                create(handoff=mutated)
        mutated = copy.deepcopy(handoff)
        mutated["plan_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "ops_handoff_checksum_mismatch"):
            create(handoff=mutated)

    def test_plan_is_deterministic_and_never_executes_commands(self):
        a, b = create(), create()
        self.assertEqual(a, b)
        self.assertEqual(
            a["plan_sha256"],
            hashlib.sha256(json.dumps(
                {k: v for k, v in a.items() if k != "plan_sha256"},
                sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest(),
        )
        root = Path(__file__).resolve().parents[2]
        source = (root / "scripts/ops_combined_ci_plan.py").read_text()
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        self.assertFalse(any(
            name in ("subprocess", "socket", "psycopg", "psutil", "os", "ctypes")
            for name in imported
        ))
        self.assertNotIn("subprocess.run(", source)
        self.assertNotIn("os.system(", source)


if __name__ == "__main__":
    unittest.main()
