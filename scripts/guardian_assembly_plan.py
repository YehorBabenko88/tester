"""Generate a non-effectful integration assembly plan from verified CI receipts."""

from __future__ import annotations

import re

FOUNDATION = "bd619eb88e9444c3b414739a27f3053fb240251e"
ROLES = ("code", "data", "storage", "exchange")
REQUIRED = frozenset(
    {
        "Python static and unit tests",
        "Platform acceptance",
        "Guardian MASTER isolated fault acceptance",
        "Pinned research PostgreSQL integration",
        "PC4 qualified snapshot PostgreSQL",
        "research execution with pinned PG snapshot",
        "combined synthetic F-G-D browser",
        "MASTER contract drift diagnosis",
    }
)


def assembly_plan(receipts: dict) -> dict:
    """Validate receipt shape only; caller must independently fetch GitHub evidence."""
    if receipts.get("foundation") != FOUNDATION:
        raise ValueError("foundation_mismatch")
    if receipts.get("guardians_enabled") is not False:
        raise ValueError("effectors_not_disabled")
    children = receipts.get("guardians", {})
    if set(children) != set(ROLES):
        raise ValueError("incomplete_guardians")
    seen = set()
    commits = []
    for role in ROLES:
        child = children[role]
        sha = child.get("sha", "")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("invalid_sha")
        if child.get("merge_base") != FOUNDATION or sha == FOUNDATION or sha in seen:
            raise ValueError("invalid_ancestry_or_duplicate")
        expected = {
            f"src/api/guardians/{role}_guardian.py",
            f"tests/platform/test_{role}_guardian.py",
            f"docs/guardians/{role}-guardian.md",
            f"docs/guardians/workstream-{role}-p0.md",
        }
        if set(child.get("paths", [])) != expected:
            raise ValueError("unexpected_owned_paths")
        runs = child.get("runs", [])
        if len(runs) != len(REQUIRED) or {r.get("name") for r in runs} != REQUIRED:
            raise ValueError("incomplete_ci")
        if any(
            r.get("head_sha") != sha
            or r.get("status") != "completed"
            or r.get("conclusion") != "success"
            or type(r.get("id")) is not int
            or r["id"] <= 0
            for r in runs
        ):
            raise ValueError("ci_not_green")
        seen.add(sha)
        commits.append({"role": role, "sha": sha, "paths": sorted(expected)})
    return {
        "foundation": FOUNDATION,
        "commits": commits,
        "status": "PLAN_ONLY_REQUIRES_INDEPENDENT_GITHUB_VERIFICATION",
        "guardians_enabled": False,
        "merge_main": False,
    }
