"""Read-only audit of MASTER's exact Git source composer against OPS opt-in scope.

This does NOT edit MASTER #77, execute composer, install services, inspect PC4,
connect to PostgreSQL or start the native launcher. Source-only AST validation.
"""
from __future__ import annotations

import ast
import hashlib
import re
from collections.abc import Iterable

from scripts.ops_native_optin_handoff import (
    CI_AND_DOC_FILES,
    RUNTIME_FILES,
    TEST_FILES,
)

SCHEMA = "ops-master-composition-scope-audit/1"
EXPECTED_BASE = "3b2eef8a4139195db1a775545e83f1c1f58bf403"
_APPROVED = frozenset((*RUNTIME_FILES, *TEST_FILES, *CI_AND_DOC_FILES))
_SHA = re.compile(r"^[0-9a-f]{40}$")


def _assignment(tree: ast.Module, name: str):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise ValueError("composer_static_assignment_missing:" + name)


def audit_master_composer(source: str, *, changed_ops_paths: Iterable[str],
                          expected_ops_sha: str,
                          expected_base_sha: str = EXPECTED_BASE) -> dict:
    """Return exact excess files and refuse to advertise a minimal opt-in copy."""
    if (not isinstance(source, str) or not 0 < len(source.encode()) <= 100_000
            or not _SHA.fullmatch(expected_ops_sha)
            or expected_base_sha != EXPECTED_BASE):
        raise ValueError("unreviewed_master_composition_source_or_pin")
    tree = ast.parse(source)
    actual_base = _assignment(tree, "BASE")
    producers = _assignment(tree, "PRODUCERS")
    if (actual_base != expected_base_sha
            or not isinstance(producers, tuple)
            or dict(producers).get("OPS") != expected_ops_sha):
        raise ValueError("master_composition_sha_pin_mismatch")
    files = list(changed_ops_paths)
    if (len(files) != len(set(files))
            or any(not isinstance(x, str) or not x for x in files)):
        raise ValueError("invalid_ops_pr_changed_file_inventory")
    excess = sorted(set(files) - _APPROVED)
    missing = sorted(_APPROVED - set(files))
    if missing:
        raise ValueError("approved_ops_handoff_files_missing_from_producer")
    # Check executable AST, not comments or a positive CI status. This is
    # source-only evidence: GitHub Actions runtime and physical PC4 remain
    # separate from a static composer file.
    guarded = (
        "CI" in source and "EXPECTED_STAGING_SHA" in source
        and "staging SHA changed" in source
    )
    protected = _assignment(tree, "PROTECTED")
    if (not guarded or not isinstance(protected, tuple)
            or not {"src/integration/pc4_runtime.py", "src/api/worker.py",
                    "pyproject.toml"} <= set(protected)):
        raise ValueError("composer_missing_immutable_or_pc4_protection")
    direct_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                direct_calls.append(fn.id)
            elif isinstance(fn, ast.Attribute):
                direct_calls.append(fn.attr)
    if any(call in direct_calls for call in (
        "Popen", "system", "runpy", "exec", "eval", "StartService",
        "CreateProcess", "kill", "terminate", "reboot",
    )):
        raise ValueError("composer_contains_unreviewed_process_activation_call")
    uses_diff = "diff" in source and "--name-status" in source
    explicit_scope = any(
        isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in {
                "OPS_FILE_ALLOWLIST", "OPS_ALLOWED_FILES",
            } for t in node.targets
        ) for node in tree.body
    )
    unbounded_ops_diff = uses_diff and not explicit_scope
    return {
        "schema": SCHEMA,
        "master_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "ops_producer_sha": expected_ops_sha,
        "approved_count": len(_APPROVED),
        "actually_composed_ops_count": len(files),
        "extra_count": len(excess),
        "extra_paths": excess,
        "approved_missing": missing,
        "ci_and_staging_sha_guards_present": guarded,
        "protected_installed_runtime_paths_guarded": True,
        "direct_native_activation_calls_in_composer": False,
        "source_audit_only_no_runtime_or_install_proof": True,
        "copies_all_ops_diff": unbounded_ops_diff,
        "explicit_ops_scope_allowlist": explicit_scope,
        "minimal_optin_copy_confirmed": not excess and explicit_scope,
        "native_installed_restart_authorized": False,
        "result": "BLOCKED_SCOPE_TOO_BROAD" if excess or unbounded_ops_diff
                  else "SOURCE_SCOPE_REVIEWED",
    }
