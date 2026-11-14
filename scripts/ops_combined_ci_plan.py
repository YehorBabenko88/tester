"""Generate a non-executing exact-head CI acceptance matrix for MASTER #77.

The returned commands are review material, NOT executed here. In particular
this module must never install/enable native services, connect to any database,
start tester, or authorize installed PC4 restart.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

from scripts.ops_native_optin_handoff import (
    HANDOFF_SCHEMA,
    PINNED_BASE,
    PINNED_PRODUCERS,
)

PLAN_SCHEMA = "ops-immutable-combined-ci-plan/1"
_SHA = re.compile(r"[a-f0-9]{40}\Z")
_ALLOWED_PRODUCERS = ("data", "research", "ui", "ops")
# GitHub-hosted runner snapshots only, never a local user's default PG5432.
_ALLOWED_PORTS = (55449, 55472, 55473, 55478, 55479, 55480, 55481)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def combined_acceptance_plan(*, combined_sha: str, ops_sha: str,
                             handoff: Mapping, source_tree_sha: str,
                             producer_shas: Mapping[str, str],
                             ephemeral_ci_only: bool = False) -> dict:
    """Return immutable test commands; never execute anything.

    The current #77 remains documentation-only. A proposed combined SHA must
    be an independently reviewed actual commit and have a distinct Git tree.
    This function cannot attest that fact; the CI first executes the supplied
    SHA assertions on a fresh, immutable checkout before any test stage.
    """
    if ephemeral_ci_only is not True:
        raise PermissionError("combined_testing_requires_explicit_ephemeral_ci")
    if (not isinstance(combined_sha, str) or not _SHA.fullmatch(combined_sha)
            or not isinstance(ops_sha, str) or not _SHA.fullmatch(ops_sha)
            or not isinstance(source_tree_sha, str) or not _SHA.fullmatch(source_tree_sha)):
        raise ValueError("exact_commit_and_tree_sha_required")
    if combined_sha in (PINNED_BASE, ops_sha, *PINNED_PRODUCERS.values()):
        raise ValueError("combined_candidate_must_be_distinct_from_producers")
    if (not isinstance(producer_shas, Mapping)
            or set(producer_shas) != set(_ALLOWED_PRODUCERS)
            or producer_shas.get("ops") != ops_sha
            or any(not isinstance(v, str) or not _SHA.fullmatch(v)
                   for v in producer_shas.values())):
        raise ValueError("incomplete_combined_producer_pin_manifest")
    if not isinstance(handoff, Mapping) or handoff.get("schema") != HANDOFF_SCHEMA:
        raise ValueError("missing_reviewed_ops_handoff")
    if (handoff.get("installed_native_activation") != "BLOCKED"
            or handoff.get("automatic_activation") is not False
            or handoff.get("ready_to_merge_or_install") is not False
            or handoff.get("requested_action") != "PREPARE_SOURCE_ONLY"
            or handoff.get("base_sha") != PINNED_BASE
            or handoff.get("producer_sha_pins") != PINNED_PRODUCERS):
        raise ValueError("unsafe_ops_handoff_or_changed_frozen_staging")
    original = {k: v for k, v in handoff.items() if k != "plan_sha256"}
    if handoff.get("plan_sha256") != _digest(original):
        raise ValueError("ops_handoff_checksum_mismatch")

    # Commands are copied from repository workflows, then grouped into
    # CI-only stages. The external runner is responsible for setting up the
    # fresh container/service and verifying the *real* tree SHA first.
    stages = [
        {
            "id": "source_and_security",
            "runner": "ubuntu-24.04",
            "dependencies": [],
            "commands": [
                "git diff --quiet",
                "git diff --cached --quiet",
                "git rev-parse HEAD",
                "git rev-parse HEAD^{tree}",
                "python -m compileall -q .",
                "python -m unittest discover -p 'test_*.py' -v",
                "python scripts/acceptance.py",
            ],
            "requires": ["requirements-dev.txt", "requirements-platform.lock"],
        },
        {
            "id": "platform_security_postgres16",
            "runner": "ubuntu-24.04",
            "dependencies": ["source_and_security"],
            "postgres": {"major": 16, "published_port": 55478,
                         "database": "tester_combined_platform_isolated"},
            "commands": [
                "python scripts/acceptance.py --integration --security",
            ],
            "requires": ["TEST_DATABASE_URL", "isolated PG16 Docker service"],
        },
        {
            "id": "postgres14_storage_migration_and_http",
            "runner": "ubuntu-24.04",
            "dependencies": ["source_and_security"],
            "postgres": {"major": 14, "published_port": 55480,
                         "database": "tester_combined_disposable_pg14"},
            "commands": [
                "python -m unittest discover -s tests/storage -v",
                "python -m unittest tests.integration.test_trade_http -v",
            ],
            "requires": ["TESTER_TEST_ADMIN_DSN", "isolated PG14 Docker service"],
        },
        {
            "id": "postgres18_storage_migration_and_http",
            "runner": "ubuntu-24.04",
            "dependencies": ["source_and_security"],
            "postgres": {"major": 18, "published_port": 55481,
                         "database": "tester_combined_disposable_pg18"},
            "commands": [
                "python -m unittest discover -s tests/storage -v",
                "python -m unittest tests.integration.test_trade_http -v",
            ],
            "requires": ["TESTER_TEST_ADMIN_DSN", "isolated PG18 Docker service"],
        },
        {
            "id": "postgres16_real_source_readback_research",
            "runner": "ubuntu-24.04",
            "dependencies": ["source_and_security"],
            "postgres": {"major": 16, "published_port": 55472,
                         "database": "tester_research_combined_ci"},
            "commands": [
                "python -m pytest tests/platform/test_research_postgres.py tests/platform/test_combined_routes.py -v",
                "python -m pytest tests/platform/test_research_execution.py tests/platform/test_research_summary.py tests/platform/test_pc4_runtime.py tests/platform/test_research_execution_postgres.py -v",
            ],
            "requires": ["TEST_RESEARCH_DATABASE_URL", "isolated PG16 Docker service"],
        },
        {
            "id": "postgres16_ops_lease_wal_and_resource",
            "runner": "ubuntu-24.04",
            "dependencies": ["source_and_security"],
            "postgres": {"major": 16, "published_port": 55479,
                         "database": "ops_isolated"},
            "commands": [
                "python -m unittest discover -s tests/platform -p 'test_ops_pg_real.py' -v",
                "python -m unittest discover -s tests/platform -p 'test_ops_pg_lifecycle_real.py' -v",
                "python -m unittest discover -s tests/platform -p 'test_ops_pg_dual_real.py' -v",
                "python -m unittest discover -s tests/platform -p 'test_ops_pg_load_real.py' -v",
                "python -m unittest discover -s tests/platform -p 'test_ops_native_admission_pg_real.py' -v",
            ],
            "requires": ["TEST_OPS_ISOLATED_DSN", "isolated PG16 Docker service"],
        },
        {
            "id": "node_chromium_ui_13tf",
            "runner": "ubuntu-24.04",
            "dependencies": ["source_and_security"],
            "working_directory": "frontend",
            "commands": [
                "npm ci --ignore-scripts",
                "npm test",
                "npx playwright install --with-deps chromium",
                "npm run test:ui",
            ],
            "requires": ["Node 22", "Chromium fixture browser"],
        },
        {
            "id": "fastapi_genuine_synthetic_f_g_d_browser",
            "runner": "ubuntu-24.04",
            "dependencies": ["source_and_security", "node_chromium_ui_13tf"],
            "postgres": {"major": 16, "published_port": 55472,
                         "database": "tester_research_combined_ci"},
            "commands": [
                "python -m pytest tests/platform/test_combined_browser_fixture.py -q",
                "python -m scripts.combined_browser_ci prepare \"$TESTER_COMBINED_FIXTURE_FILE\"",
                "python -m scripts.combined_browser_ci serve \"$TESTER_COMBINED_FIXTURE_FILE\"",
                "python -m scripts.combined_browser_ci gateway \"$TESTER_COMBINED_FIXTURE_FILE\"",
                "npx playwright test -c playwright.combined.config.js --reporter=line",
                "python -m scripts.combined_browser_ci verify \"$TESTER_COMBINED_FIXTURE_FILE\"",
            ],
            "requires": ["TESTER_COMBINED_CI=synthetic-disposable",
                         "TEST_RESEARCH_DATABASE_URL", "API process owned by CI"],
            "orchestration": "serve subprocess must run under CI-owned background guard and graceful cleanup",
        },
        {
            "id": "win32_native_original_handle_and_rollback",
            "runner": "windows-2025",
            "dependencies": ["source_and_security"],
            "commands": [
                "python -m unittest discover -s tests/platform -p test_ops_native_windows_process.py -v",
                "python -m unittest discover -s tests/platform -p test_ops_native_fixture_launcher.py -v",
                "python -m unittest discover -s tests/platform -p test_ops_native_optin_handoff.py -v",
            ],
            "requires": ["GITHUB_ACTIONS=true", "disposable isolated child processes"],
        },
        {
            "id": "windows_installer_isolation",
            "runner": "windows-latest",
            "dependencies": ["source_and_security"],
            "commands": [
                "pwsh -File ./scripts/windows/Test-Isolated.ps1",
            ],
            "requires": ["fresh hosted CI Windows VM only", "explicit isolated test setup"],
        },
    ]
    all_ports = [
        stage["postgres"]["published_port"] for stage in stages if "postgres" in stage
    ]
    if any(port not in _ALLOWED_PORTS or port == 5432 for port in all_ports):
        raise ValueError("unapproved_database_port")
    plan = {
        "schema": PLAN_SCHEMA,
        "combined_head_sha": combined_sha,
        "combined_tree_sha": source_tree_sha,
        "producer_pins": dict(producer_shas),
        "ops_handoff_sha256": handoff["plan_sha256"],
        "source_checkout_policy": "EXACT_SHA_NO_BRANCH_FALLBACK",
        "source_checkout_verification": [
            'test "$(git rev-parse HEAD)" = "' + combined_sha + '"',
            'test "$(git rev-parse HEAD^{tree})" = "' + source_tree_sha + '"',
        ],
        "stages": stages,
        "execution": "NOT_RUN",
        "all_stages_must_pass": True,
        "staging_release_authorized": False,
        "installed_native_activation": "BLOCKED",
        "automatic_native_start": False,
        "physical_pc4_access": False,
        "production_pg5432_access": False,
        "real_trading": False,
        "commands_executed_by_this_module": False,
        "status": "COMBINED_CANDIDATE_REQUIRES_MASTER_COMPOSITION_AND_CI",
    }
    return {**plan, "plan_sha256": _digest(plan)}
