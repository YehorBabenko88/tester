"""Source-only opt-in handoff for MASTER #77. NEVER activates a native launcher.

A positive source compatibility report permits creating a REVIEW PLAN only.
This module has no subprocess, installer, filesystem or PostgreSQL mutation
capability. Native installed restart requires a separate future reviewed
implementation with physical operator approval and independent Win32 owner.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

from scripts.ops_candidate_compatibility import SCHEMA

HANDOFF_SCHEMA = "ops-native-optin-handoff/1"
PREPARE_ONLY = "PREPARE_SOURCE_ONLY"
PINNED_BASE = "3b2eef8a4139195db1a775545e83f1c1f58bf403"
PINNED_PRODUCERS = {
    "data": "f78349b1ad7bb754baee380f59bca67f2b1d2009",
    "research": "b11b79832d1b0d848773ff077a36e7cb0595fdcb",
    "ui": "fd151168473f4add97ee286b008d4540b49ac566",
}
# Exact OPS-only source paths for a deliberate handoff; never drag in unrelated
# installer/service files or replace the installed API/worker by side effect.
RUNTIME_FILES = (
    "scripts/ops_admission_pg.py",
    "scripts/ops_candidate_compatibility.py",
    "scripts/ops_native_admission.py",
    "scripts/ops_native_fixture_launcher.py",
    "scripts/ops_native_optin_handoff.py",
    "scripts/ops_native_scoped_stop.py",
    "scripts/ops_native_windows_rollback.py",
    "scripts/ops_native_windows_witness.py",
    "scripts/ops_pg_isolation.py",
    "scripts/ops_resource_metrics.py",
    "scripts/ops_win32_handle.py",
)
TEST_FILES = (
    "tests/platform/test_ops_candidate_compatibility.py",
    "tests/platform/test_ops_combined_ci_plan.py",
    "tests/platform/test_ops_native_admission.py",
    "tests/platform/test_ops_native_admission_pg_real.py",
    "tests/platform/test_ops_native_fixture_launcher.py",
    "tests/platform/test_ops_native_optin_handoff.py",
    "tests/platform/test_ops_native_scoped_stop.py",
    "tests/platform/test_ops_native_windows_process.py",
    "tests/platform/test_ops_native_windows_rollback.py",
    "tests/platform/test_ops_native_windows_witness.py",
    "tests/platform/test_ops_resource_metrics.py",
)
CI_AND_DOC_FILES = (
    ".github/workflows/ops-portable.yml",
    "scripts/ops_combined_ci_plan.py",
    "docs/ops_native_fixture_launcher_20261113.md",
    "docs/ops_native_launcher_admission_20261113.md",
    "docs/ops_native_handoff_77_20261114.md",
    "docs/ops_77_combined_ci_commands_20261114.md",
)
FORBIDDEN_IMPLICIT_PATCHES = (
    "src/api/main.py",
    "src/api/worker.py",
    "src/integration/pc4_runtime.py",
    "main",
    "installer",
    "windows_service",
)
MANDATORY_GATES = (
    "real_api_worker_generation_scoped_stop_not_accepted",
    "persistent_external_original_win32_handle_guardian_not_accepted",
    "atomic_postgres_and_win32_startup_lease_not_accepted",
    "real_windows_and_postgres_combined_e2e_not_accepted",
    "physical_pc4_operator_acceptance_not_authorized",
)
_HEX = re.compile(r"[a-f0-9]{64}\Z")


def _sha(obj: object) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def prepare_native_source_handoff(
    compatibility_report: Mapping, *,
    producer_heads: Mapping[str, str],
    opt_in: str | None = None,
) -> dict:
    """Create a deterministic REVIEW-ONLY file selection; no enablement path.

    Opt-in permits sharing an integration proposal, not starting, installing,
    stopping or updating native services or any user PostgreSQL cluster.
    """
    if opt_in != PREPARE_ONLY:
        raise PermissionError("native_handoff_requires_explicit_prepare_only")
    if not isinstance(compatibility_report, Mapping):
        raise ValueError("missing_readonly_compatibility_report")
    if (compatibility_report.get("schema") != SCHEMA
            or compatibility_report.get("installed_native_activation") != "BLOCKED"
            or compatibility_report.get("pc4_restart_authorized") is not False
            or compatibility_report.get("default_activation_enabled") is not False
            or compatibility_report.get("pg5432_access") is not False):
        raise ValueError("candidate_attempts_unsafe_native_activation")
    source_hashes = compatibility_report.get("source_digests")
    ui_hash = compatibility_report.get("ui_api_sha256")
    if (not isinstance(source_hashes, dict)
            or set(source_hashes) != {"pc4", "api", "worker", "data", "research"}
            or any(not isinstance(h, str) or not _HEX.fullmatch(h)
                   for h in source_hashes.values())
            or not isinstance(ui_hash, str) or not _HEX.fullmatch(ui_hash)):
        raise ValueError("unqualified_candidate_source_digest")
    digest_body = {
        "schema": SCHEMA,
        "source_digests": source_hashes,
        "ui_api_sha256": ui_hash,
    }
    if compatibility_report.get("manifest_sha256") != _sha(digest_body):
        raise ValueError("source_manifest_tampered")
    if (not isinstance(producer_heads, Mapping)
            or dict(producer_heads) != PINNED_PRODUCERS):
        raise ValueError("staging_77_producer_pin_mismatch")
    blockers = compatibility_report.get("blockers")
    if not isinstance(blockers, list) or any(not isinstance(x, str) for x in blockers):
        raise ValueError("unqualified_candidate_blocker_evidence")
    # Unresolved installed native lifecycle is expected: it remains BLOCKED.
    # But a mismatched DATA/RESEARCH/UI source interface is not a coherent
    # handoff candidate, even for source-only preview.
    incompatible = {
        "data_research_execution_contract_version_mismatch",
        "data_research_bounded_page_limit_mismatch",
        "data_read_interface_signature_mismatch",
        "research_read_interface_signature_mismatch",
        "missing_expected_data_interface",
        "missing_expected_research_interface",
        "ui_capability_route_not_declared",
    }
    if incompatible.intersection(blockers):
        raise ValueError("staging_producer_contracts_incompatible")
    files = (*RUNTIME_FILES, *TEST_FILES, *CI_AND_DOC_FILES)
    if len(files) != len(set(files)) or any(
        path in FORBIDDEN_IMPLICIT_PATCHES or ".." in path.split("/")
        for path in files
    ):
        raise ValueError("unsafe_ops_handoff_file_selection")
    plan = {
        "schema": HANDOFF_SCHEMA,
        "base_sha": PINNED_BASE,
        "producer_sha_pins": dict(producer_heads),
        "reviewed_source_compatibility_digest": compatibility_report["manifest_sha256"],
        "requested_action": PREPARE_ONLY,
        "runtime_files": list(RUNTIME_FILES),
        "test_files": list(TEST_FILES),
        "ci_and_docs": list(CI_AND_DOC_FILES),
        "excluded_auto_activation_targets": list(FORBIDDEN_IMPLICIT_PATCHES),
        "mandatory_unsatisfied_gates": list(MANDATORY_GATES),
        "source_scope": "OPS_ONLY_MANUAL_COPY_AFTER_MASTER_REVIEW",
        "installed_native_activation": "BLOCKED",
        "automatic_activation": False,
        "process_spawn_performed": False,
        "database_access_performed": False,
        "physical_pc4_action_performed": False,
        "ready_to_merge_or_install": False,
    }
    return {**plan, "plan_sha256": _sha(plan)}


def require_live_native_activation(_plan: object) -> None:
    """No live activation implementation exists in this source-only handoff."""
    raise PermissionError("native_installed_activation_unimplemented_and_blocked")
