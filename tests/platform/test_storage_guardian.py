"""Independent Storage Guardian P0 negative tests; disposable directories only."""
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.api.guardians.storage_guardian import (
    MAX_ENTRIES,
    _safe,
    cleanup_authorization,
    inventory,
)


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


def test_missing_tmp(tmp_path):
    assert inventory(tmp_path, min_free_bytes=0, quota_bytes=1, enabled=True)["status"] == "ALERT_UNVERIFIED_ROOT"


def test_exact_age_boundary_and_young(tmp_path):
    folder = tmp_path / "tmp"
    folder.mkdir()
    for name, stamp in (("boundary.part", 13600), ("young.part", 13601)):
        path = folder / name
        path.write_bytes(b"x")
        os.utime(path, (stamp, stamp))
    receipt = inventory(tmp_path, min_free_bytes=0, quota_bytes=100,
                        now=100000, enabled=True)
    assert [item["name"] for item in receipt["candidates"]] == ["boundary.part"]


def test_directory_part_not_selected(tmp_path):
    folder = tmp_path / "tmp"
    folder.mkdir()
    (folder / "fake.part").mkdir()
    result = inventory(tmp_path, min_free_bytes=0, quota_bytes=100,
                       now=100000, enabled=True)
    assert result["candidates"] == []


def test_limit_10001(tmp_path):
    folder = tmp_path / "tmp"
    folder.mkdir()
    for i in range(MAX_ENTRIES + 1):
        (folder / f"{i}.part").touch()
    result = inventory(tmp_path, min_free_bytes=0, quota_bytes=1, enabled=True)
    assert result["status"] == "ALERT_SCAN_LIMIT"
    assert result["candidates"] == []


@pytest.mark.parametrize("clock", [float("nan"), float("inf"), -1, True, "bad"])
def test_invalid_clock(tmp_path, clock):
    result = inventory(tmp_path, min_free_bytes=0, quota_bytes=1,
                       now=clock, enabled=True)
    assert result["status"] == "ALERT_INVALID_CLOCK"


def test_symlink_ancestor(tmp_path):
    real = tmp_path / "real"
    (real / "tmp").mkdir(parents=True)
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink privilege unavailable")
    assert inventory(link, min_free_bytes=0, quota_bytes=1,
                     enabled=True)["status"] == "ALERT_UNVERIFIED_ROOT"


def test_reparse_flag_simulation(tmp_path):
    (tmp_path / "tmp").mkdir()
    original = Path.lstat

    class FakeStat:
        def __init__(self, actual):
            self.st_mode = actual.st_mode
            self.st_file_attributes = 0x400

    def injected(path):
        actual = original(path)
        return FakeStat(actual) if path == tmp_path / "tmp" else actual

    with patch.object(Path, "lstat", injected):
        assert not _safe(tmp_path / "tmp")


def test_toctou_root_revalidation(tmp_path):
    folder = tmp_path / "tmp"
    folder.mkdir()
    target = folder / "old.part"
    target.write_text("still here")
    os.utime(target, (100, 100))
    checks = 0

    def changed(path):
        nonlocal checks
        if path == folder:
            checks += 1
            if checks == 3:
                return False
        return _safe(path)

    with patch("src.api.guardians.storage_guardian._safe", side_effect=changed):
        result = inventory(tmp_path, min_free_bytes=0, quota_bytes=100,
                           now=100000, enabled=True)
    assert checks >= 3
    assert result["status"] == "ALERT_ROOT_CHANGED"
    assert result["candidates"] == []
    assert target.exists()


def test_toctou_file_replaced_with_link(tmp_path):
    folder = tmp_path / "tmp"
    folder.mkdir()
    target = folder / "old.part"
    target.write_text("keep")
    os.utime(target, (100, 100))

    def changed(path):
        return False if path == target else _safe(path)

    with patch("src.api.guardians.storage_guardian._safe", side_effect=changed):
        result = inventory(tmp_path, min_free_bytes=0, quota_bytes=100,
                           now=100000, enabled=True)
    assert result["candidates"] == []
    assert target.exists()
