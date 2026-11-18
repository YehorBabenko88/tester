"""Independent Storage Guardian P0 negative tests; disposable directories only."""
import os
import time
from pathlib import Path

import pytest

from src.api.guardians.storage_guardian import cleanup_authorization, inventory


def test_default_disabled(tmp_path):
    (tmp_path / "tmp").mkdir()
    assert inventory(tmp_path, min_free_bytes=0, quota_bytes=100)["status"] == "DISABLED"


def test_aged_direct_part_only(tmp_path):
    folder = tmp_path / "tmp"
    folder.mkdir()
    old = folder / "old.part"
    old.write_bytes(b"old")
    os.utime(old, (100, 100))
    young = folder / "young.part"
    young.write_bytes(b"young")
    other = folder / "history.db"
    other.write_bytes(b"keep")
    os.utime(other, (100, 100))
    nested = folder / "nested"
    nested.mkdir()
    (nested / "old.part").write_bytes(b"nested")
    receipt = inventory(tmp_path, min_free_bytes=0, quota_bytes=1000,
                        now=100 + 86401, enabled=True)
    assert receipt["status"] == "OBSERVED"
    assert [c["name"] for c in receipt["candidates"]] == ["old.part"]
    assert receipt["bytes_reclaimable"] == 3
    assert receipt["evidence_sha256"] and len(receipt["evidence_sha256"]) == 64
    assert old.exists() and young.exists() and other.exists()


@pytest.mark.parametrize("budget,quota", [(-1, 10), (0, 0), (True, 10), (0, True)])
def test_invalid_budgets(tmp_path, budget, quota):
    (tmp_path / "tmp").mkdir()
    assert inventory(tmp_path, min_free_bytes=budget, quota_bytes=quota,
                     enabled=True)["status"] == "ALERT_INVALID_BUDGET"


def test_missing_root_and_path_traversal(tmp_path):
    assert inventory(tmp_path / "missing", min_free_bytes=0,
                     quota_bytes=1, enabled=True)["status"] == "ALERT_UNVERIFIED_ROOT"
    (tmp_path / "tmp").mkdir()
    assert inventory(tmp_path / "..", min_free_bytes=0,
                     quota_bytes=1, enabled=True)["status"] == "ALERT_UNVERIFIED_ROOT"


def test_symlink_entry_never_selected(tmp_path):
    folder = tmp_path / "tmp"
    folder.mkdir()
    outside = tmp_path / "outside.part"
    outside.write_bytes(b"secret")
    os.utime(outside, (100, 100))
    try:
        (folder / "linked.part").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted")
    result = inventory(tmp_path, min_free_bytes=0, quota_bytes=100,
                       now=100000, enabled=True)
    assert result["candidates"] == []
    assert "ALERT_REPARSE_ENTRY" in result["proposals"]
    assert outside.read_bytes() == b"secret"


def test_symlink_tmp_rejected(tmp_path):
    target = tmp_path / "actual"
    target.mkdir()
    try:
        (tmp_path / "tmp").symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted")
    assert inventory(tmp_path, min_free_bytes=0, quota_bytes=1,
                     enabled=True)["status"] == "ALERT_UNVERIFIED_ROOT"


def test_low_disk_is_proposal_only(tmp_path):
    (tmp_path / "tmp").mkdir()
    result = inventory(tmp_path, min_free_bytes=10**30, quota_bytes=1,
                       enabled=True)
    assert "PROPOSE_PAUSE_NEW_DOWNLOADS" in result["proposals"]


def test_no_executor(tmp_path):
    with pytest.raises(PermissionError):
        cleanup_authorization(tmp_path, enabled=True)
