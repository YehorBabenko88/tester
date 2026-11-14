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
    # MASTER currently uses 'git diff --name-status BASE..SHA' without
    # an OPS-only allowlist: all OPS PR changes are copied. The result is a
    # source composition smoke, not proof that a narrow opt-in deployment
    # list was applied.
    uses_diff = "diff" in source and "--name-status" in source
    has_allowlist = any(
        isinstance(node, ast.Constant) and
        isinstance(node.value, str) and node.value.startswith("scripts/ops_native_")
        for node in ast.walk(tree)
    )
    return {
        "schema": SCHEMA,
        "master_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "ops_producer_sha": expected_ops_sha,
        "approved_count": len(_APPROVED),
        "actually_composed_ops_count": len(files),
        "extra_count": len(excess),
        "extra_paths": excess,
        "approved_missing": missing,
        "copies_all_ops_diff": uses_diff and not has_allowlist,
        "minimal_optin_copy_confirmed": not excess and uses_diff,
        "automatic_activation_observed_in_source_audit": False,
        "native_installed_restart_authorized": False,
        "result": "BLOCKED_SCOPE_TOO_BROAD" if excess else "SOURCE_SCOPE_REVIEWED",
    }
