"""Fail-closed, side-effect-free Windows TESTER lifecycle sequencing.

Every adapter action must reattest actual Windows SCM/process/filesystem state.
This module generates a plan only: never installs, starts, stops or deletes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.api.guardians.windows_install_gate import canonical, uninstall_authorized, validate_manifest
from src.api.guardians.windows_install_plan import ROOT, plan_install

Action = Literal["install", "start", "stop", "uninstall"]
OWNED_ROLES = frozenset({"postgres", "api", "worker"})


@dataclass(frozen=True)
class LifecyclePlan:
    allowed: bool
    reason: str
    steps: tuple[str, ...] = ()


def lifecycle_plan(
    action: Action,
    manifest: dict,
    attested: dict,
    *,
    signed_bundle_verified: bool = False,
    typed_confirmation: str = "",
    second_confirmation: bool = False,
) -> LifecyclePlan:
    """Describe ordering only; authorization expires before any real OS operation."""
    if action not in ("install", "start", "stop", "uninstall"):
        return LifecyclePlan(False, "unknown_action")
    owned = validate_manifest(manifest, attested=attested)
    if not owned.allowed:
        return LifecyclePlan(False, owned.reason)
    if canonical(manifest.get("root")) != ROOT:
        return LifecyclePlan(False, "wrong_root")
    if signed_bundle_verified is not True:
        return LifecyclePlan(False, "unsigned_release")
    if attested.get("lock_exclusive") is not True:
        return LifecyclePlan(False, "exclusive_lock_missing")
    if attested.get("runtime_identity_verified") is not True:
        return LifecyclePlan(False, "runtime_identity_unverified")
    if attested.get("system_pg_5432_unchanged") is not True:
        return LifecyclePlan(False, "system_postgres_not_isolated")
    roles = manifest.get("service_roles")
    if not isinstance(roles, dict) or set(roles) != OWNED_ROLES:
        return LifecyclePlan(False, "missing_service_roles")
    if set(roles.values()) != set(manifest["services"]):
        return LifecyclePlan(False, "foreign_service_role")
    if action == "install":
        candidate = plan_install(manifest, attested, signed_bundle_verified=True)
        if not candidate.allowed:
            return LifecyclePlan(False, candidate.reason)
        if attested.get("existing_install") is not False:
            return LifecyclePlan(False, "existing_install_requires_separate_migration")
        return LifecyclePlan(
            True,
            "plan_only",
            (
                "stage_exact_signed_release",
                "install_owned_postgresql_non5432",
                "install_owned_api",
                "install_owned_worker",
                "set_bounded_boot_recovery",
                "verify_localhost_health",
                "create_exact_two_shortcuts",
                "record_owned_manifest_receipt",
            ),
        )
    if action == "start":
        if attested.get("owned_services_stopped_or_running") is not True:
            return LifecyclePlan(False, "foreign_service_state")
        return LifecyclePlan(
            True,
            "plan_only",
            (
                "verify_postgresql_ownership",
                "start_owned_postgresql",
                "wait_for_owned_database",
                "start_owned_api",
                "start_owned_worker",
                "verify_localhost_health",
                "open_localhost_terminal",
            ),
        )
    if action == "stop":
        return LifecyclePlan(
            True,
            "plan_only",
            ("request_worker_drain", "stop_owned_worker", "stop_owned_api", "stop_owned_postgresql"),
        )
    if not uninstall_authorized(typed_confirmation, second_confirmation, owned):
        return LifecyclePlan(False, "delete_confirmation_missing")
    if attested.get("all_owned_processes_exited") is not True:
        return LifecyclePlan(False, "live_processes_block_deletion")
    if attested.get("all_owned_services_stopped") is not True:
        return LifecyclePlan(False, "live_services_block_deletion")
    if attested.get("owner_paths_rechecked") is not True:
        return LifecyclePlan(False, "delete_paths_not_reattested")
    if ROOT not in {canonical(p) for p in manifest["delete_targets"]}:
        return LifecyclePlan(False, "incomplete_delete_scope")
    return LifecyclePlan(
        True,
        "plan_only",
        (
            "reverify_exclusive_lock_and_owner",
            "verify_all_owned_processes_exited",
            "remove_owned_worker_and_api_services",
            "remove_owned_postgresql_service",
            "verify_system_postgresql_5432_unchanged",
            "delete_all_attested_tester_owned_data_and_history",
            "delete_all_attested_tester_owned_results_and_logs",
            "delete_attested_tester_shortcuts",
            "record_nonsecret_partial_failure_status",
        ),
    )
