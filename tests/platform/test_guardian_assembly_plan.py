"""Negative tests for integration receipt planning; no Git or host effects."""

from copy import deepcopy

import pytest

from scripts.guardian_assembly_plan import FOUNDATION, REQUIRED, ROLES, assembly_plan


def fixture():
    children = {}
    for i, role in enumerate(ROLES, 1):
        sha = str(i) * 40
        children[role] = {
            "sha": sha,
            "merge_base": FOUNDATION,
            "paths": [
                f"src/api/guardians/{role}_guardian.py",
                f"tests/platform/test_{role}_guardian.py",
                f"docs/guardians/{role}-guardian.md",
                f"docs/guardians/workstream-{role}-p0.md",
            ],
            "runs": [
                {
                    "name": name,
                    "id": i * 100 + j,
                    "head_sha": sha,
                    "status": "completed",
                    "conclusion": "success",
                }
                for j, name in enumerate(sorted(REQUIRED), 1)
            ],
        }
    return {"foundation": FOUNDATION, "guardians_enabled": False, "guardians": children}


def test_assembly_plan_never_authorizes_main_or_effectors():
    result = assembly_plan(fixture())
    assert len(result["commits"]) == 4
    assert result["merge_main"] is False
    assert result["guardians_enabled"] is False


@pytest.mark.parametrize("status", ["queued", "in_progress", "cancelled", "skipped", "failure"])
def test_incomplete_or_failed_ci_blocks_assembly(status):
    receipt = fixture()
    run = receipt["guardians"]["code"]["runs"][0]
    run["conclusion"] = status
    with pytest.raises(ValueError, match="ci_not_green"):
        assembly_plan(receipt)


def test_stale_ci_and_foreign_paths_block_assembly():
    receipt = fixture()
    stale = deepcopy(receipt)
    stale["guardians"]["data"]["runs"][0]["head_sha"] = "f" * 40
    with pytest.raises(ValueError, match="ci_not_green"):
        assembly_plan(stale)
    receipt["guardians"]["storage"]["paths"].append("src/api/guardian_policy.py")
    with pytest.raises(ValueError, match="unexpected_owned_paths"):
        assembly_plan(receipt)
