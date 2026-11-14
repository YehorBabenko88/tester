"""Read-only OPS compatibility gate for proposed MASTER #77 source composition.

Never starts processes, connects to PostgreSQL, installs a service, or grants
native restart authorization. Source evidence is tied to caller-supplied bytes;
a review/CI manifest cannot substitute for an OS birth-bound exit witness.
"""
from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Mapping

MAX_SOURCE_BYTES = 512 * 1024
ROLES = ("pc4", "api", "worker", "data", "research")
REQUIRED_METHODS = {
    "pc4": ("serve",),
    "api": ("create_app", "run"),
    "worker": ("run", "process_one"),
    "data": ("read_execution_events", "read_research_trades"),
    "research": ("read_data_metrics", "require_strategy_lab_f_input"),
}
SCOPED_MARKERS = frozenset({
    "OPS_GENERATION", "OPS_OWNER_MARKER", "OPS_CONFIG_SHA256",
    "OPS_STOP_PATH", "OPS_CHECKPOINT_PATH",
})
SCHEMA = "ops-candidate-readonly-compatibility/1"


def _inspect_source(label: str, source: str) -> dict:
    if not isinstance(source, str) or not 0 < len(source.encode("utf-8")) <= MAX_SOURCE_BYTES:
        raise ValueError("missing_or_oversized_candidate_source:" + label)
    try:
        tree = ast.parse(source, filename=label)
    except SyntaxError as exc:
        raise ValueError("invalid_candidate_python:" + label) from exc
    funcs = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing = sorted(set(REQUIRED_METHODS[label]) - funcs)
    strings = {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    # Whitespace/comment/docs do not authorize capabilities. Only Python
    # string constants used by the parsed program count as signal material.
    nested_strings = set()
    for value in strings:
        nested_strings.update(marker for marker in SCOPED_MARKERS if marker in value)
    return {
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "required_functions_present": not missing,
        "missing_functions": missing,
        "markers": sorted(nested_strings),
        "stop_request_legacy": any("stop.request" in value for value in strings),
        "pc4_shutdown_legacy": any("pc4-shutdown.request" in value for value in strings),
        "capabilities_route": any("/api/capabilities" in value for value in strings),
        "unknown_coverage_explicit": any("UNKNOWN" in value for value in strings),
    }


def review_candidate_sources(sources: Mapping[str, str], *,
                             ui_api_source: str) -> dict:
    """Source-only readiness report, always DENY installed native activation.

    The installed API/worker lack a durable generation-scoped lifecycle;
    this scanner does NOT attempt to infer a runtime capability from comments,
    marker names, saved PIDs, or positive unit/CI fixtures.
    """
    if not isinstance(sources, Mapping) or set(sources) != set(ROLES):
        raise ValueError("candidate_source_scope_incomplete")
    inspected = {role: _inspect_source(role, sources[role]) for role in ROLES}
    api_ui = _inspect_source("api", ui_api_source)
    blockers = []
    for role in ROLES:
        if not inspected[role]["required_functions_present"]:
            blockers.append("missing_expected_" + role + "_interface")
    if not api_ui["capabilities_route"]:
        blockers.append("ui_capability_route_not_declared")
    if not inspected["worker"]["stop_request_legacy"]:
        blockers.append("worker_stop_contract_not_identified")
    if not inspected["api"]["stop_request_legacy"]:
        blockers.append("api_stop_contract_not_identified")
    if not inspected["pc4"]["pc4_shutdown_legacy"]:
        blockers.append("pc4_stop_contract_not_identified")
    if not inspected["research"]["unknown_coverage_explicit"]:
        blockers.append("research_unknown_coverage_contract_not_identified")
    # Merely finding all names is not proof that actual installed startup,
    # cooperative drain, original Win32 handles and PostgreSQL fencing agree.
    for role in ("pc4", "api", "worker"):
        if not SCOPED_MARKERS.issubset(inspected[role]["markers"]):
            blockers.append(role + "_generation_scoped_stop_not_implemented")
    blockers.extend((
        "persistent_external_original_handle_guardian_not_implemented",
        "installed_api_worker_generation_ack_not_tested",
        "atomic_pg_incarnation_lease_reservation_not_implemented",
        "native_windows_and_pg_combined_physical_e2e_not_run",
    ))
    digest_payload = {
        "schema": SCHEMA,
        "source_digests": {role: inspected[role]["source_sha256"] for role in ROLES},
        "ui_api_sha256": api_ui["source_sha256"],
    }
    digest_value = hashlib.sha256(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        **digest_payload,
        "manifest_sha256": digest_value,
        "interfaces": inspected,
        "ui_api": api_ui,
        "blockers": sorted(set(blockers)),
        "installed_native_activation": "BLOCKED",
        "default_activation_enabled": False,
        "runtime_source_attested_by_ci": False,
        "pg5432_access": False,
        "pc4_restart_authorized": False,
    }


def require_installed_native_activation(report: dict) -> None:
    """Explicit fail-closed production boundary; nothing can enable it here."""
    if not isinstance(report, dict) or report.get("schema") != SCHEMA:
        raise ValueError("invalid_candidate_contract_report")
    if report.get("installed_native_activation") != "BLOCKED":
        raise ValueError("unreviewed_native_enablement_attempt")
    raise RuntimeError("installed_native_launcher_requires_independent_guardian_and_physical_acceptance")
