"""Independent Storage Guardian P0 negative tests; disposable directories only."""
import os
from pathlib import Path
from types import SimpleNamespace
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
    assert result["status"] == "ALERT_REPARSE_ENTRY"
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
            if checks == 2:
                return False
        return _safe(path)

    with patch("src.api.guardians.storage_guardian._safe", side_effect=changed):
        result = inventory(tmp_path, min_free_bytes=0, quota_bytes=100,
                           now=100000, enabled=True)
    assert checks == 2
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


def test_candidate_identity_swap_fail_closed(tmp_path):
    """Inject a changing size on the second stat; manifest must be discarded."""
    folder = tmp_path / "tmp"
    folder.mkdir()
    target = folder / "swap.part"
    target.write_bytes(b"safe")
    os.utime(target, (100, 100))
    original = Path.lstat
    calls = 0

    class ChangedStat:
        def __init__(self, source):
            self.__dict__.update({
                "st_dev": source.st_dev, "st_ino": source.st_ino,
                "st_mode": source.st_mode, "st_size": source.st_size + 1,
                "st_mtime_ns": source.st_mtime_ns,
                "st_file_attributes": getattr(source, "st_file_attributes", 0),
            })

    def injected(path):
        nonlocal calls
        info = original(path)
        if path == target:
            calls += 1
            if calls == 3:
                return ChangedStat(info)
        return info

    with patch.object(Path, "lstat", injected):
        result = inventory(tmp_path, min_free_bytes=0, quota_bytes=100,
                           now=100000, enabled=True)
    assert result["status"] == "ALERT_FILE_CHANGED"
    assert result["candidates"] == []
    assert target.read_bytes() == b"safe"


def test_dry_run_does_not_modify_any_file(tmp_path):
    folder = tmp_path / "tmp"
    folder.mkdir()
    protected = {}
    for name in ("old.part", "young.part", "backup.part.bak", "pg_wal", "history.db"):
        path = folder / name
        path.write_bytes(name.encode())
        protected[name] = path.read_bytes()
        if name != "young.part":
            os.utime(path, (100, 100))
    # Freeze the external disk telemetry: the evidence hash includes free space.
    usage = SimpleNamespace(free=100000, total=200000)
    with patch("src.api.guardians.storage_guardian.shutil.disk_usage", return_value=usage):
        first = inventory(tmp_path, min_free_bytes=0, quota_bytes=1,
                          now=100000, enabled=True)
        second = inventory(tmp_path, min_free_bytes=0, quota_bytes=1,
                           now=100000, enabled=True)
    assert first["status"] == second["status"] == "OBSERVED"
    assert first["evidence_sha256"] == second["evidence_sha256"]
    assert [item["name"] for item in first["candidates"]] == ["old.part"]
    assert all((folder / name).read_bytes() == content
               for name, content in protected.items())


def test_invalid_root_relative_and_missing(tmp_path):
    assert inventory("relative/runtime", min_free_bytes=0, quota_bytes=100,
                     enabled=True)["status"] == "ALERT_UNVERIFIED_ROOT"
    assert inventory(tmp_path / "tmp" / "..", min_free_bytes=0,
                     quota_bytes=100, enabled=True)["status"] == "ALERT_UNVERIFIED_ROOT"


def test_scan_limit_exactly_10000(tmp_path):
    folder = tmp_path / "tmp"
    folder.mkdir()
    for i in range(MAX_ENTRIES):
        (folder / f"entry-{i}.txt").touch()
    result = inventory(tmp_path, min_free_bytes=0, quota_bytes=1,
                       enabled=True)
    assert result["status"] == "OBSERVED"
    assert result["candidates"] == []


def test_reparse_poison_discards_other_candidates(tmp_path):
    """One unsafe entry invalidates the entire manifest, not only itself."""
    folder = tmp_path / "tmp"
    folder.mkdir()
    old = folder / "a.part"
    old.write_bytes(b"old")
    os.utime(old, (100, 100))
    unsafe = folder / "z.part"
    unsafe.write_bytes(b"unsafe")
    original = _safe

    def poisoned(path):
        return False if path == unsafe else original(path)

    with patch("src.api.guardians.storage_guardian._safe", side_effect=poisoned):
        result = inventory(tmp_path, min_free_bytes=0, quota_bytes=100,
                           now=100000, enabled=True)
    assert result["status"] == "ALERT_REPARSE_ENTRY"
    assert result["candidates"] == []
    assert result["bytes_reclaimable"] == 0
    assert old.read_bytes() == b"old"


def test_disposable_tree_cannot_be_reinterpreted_as_owned_root(tmp_path):
    """A foreign sibling and traversal spelling cannot authorize a manifest."""
    owned = tmp_path / "TesterLab"
    (owned / "tmp").mkdir(parents=True)
    foreign = tmp_path / "postgresql-system"
    foreign.mkdir()
    sentinel = foreign / "postgresql.conf"
    sentinel.write_text("port=5432")
    old = owned / "tmp" / "old.part"
    old.write_bytes(b"temporary")
    os.utime(old, (100, 100))
    valid = inventory(owned, min_free_bytes=0, quota_bytes=1000,
                      now=100000, enabled=True)
    assert [entry["name"] for entry in valid["candidates"]] == ["old.part"]
    invalid = inventory(owned / "tmp" / "..", min_free_bytes=0,
                        quota_bytes=1000, now=100000, enabled=True)
    assert invalid["status"] == "ALERT_UNVERIFIED_ROOT"
    assert sentinel.read_text() == "port=5432"
    assert old.read_bytes() == b"temporary"


def test_no_uninstall_or_cleanup_capability(tmp_path):
    """Even explicit destructive confirmation cannot enable Storage P0."""
    with pytest.raises(PermissionError):
        cleanup_authorization(tmp_path, confirmation="DELETE TESTER", enabled=True)


def test_directory_identity_swap_discard_manifest(tmp_path):
    """Replace tmp's identity during the final check, not via a symlink."""
    folder = tmp_path / "tmp"
    folder.mkdir()
    target = folder / "old.part"
    target.write_bytes(b"intact")
    os.utime(target, (100, 100))
    original = Path.lstat
    seen_tmp = 0

    class SwappedStat:
        def __init__(self, value):
            self.st_dev = value.st_dev
            self.st_ino = value.st_ino + 1
            self.st_mode = value.st_mode
            self.st_size = value.st_size
            self.st_mtime_ns = value.st_mtime_ns
            self.st_file_attributes = getattr(value, "st_file_attributes", 0)

    def switched(path):
        nonlocal seen_tmp
        current = original(path)
        if path == folder:
            seen_tmp += 1
            if seen_tmp == 6:
                return SwappedStat(current)
        return current

    with patch.object(Path, "lstat", switched):
        result = inventory(tmp_path, min_free_bytes=0, quota_bytes=1000,
                           now=100000, enabled=True)
    assert seen_tmp == 6
    assert result["status"] == "ALERT_ROOT_CHANGED"
    assert result["candidates"] == []
    assert result["bytes_reclaimable"] == 0
    assert target.read_bytes() == b"intact"


def test_no_delete_or_maintenance_effectors_even_when_enabled(tmp_path):
    """Inventory is safe against accidental reuse of maintenance.cleanup_temp."""
    folder = tmp_path / "tmp"
    folder.mkdir()
    candidate = folder / "old.part"
    candidate.write_bytes(b"intact")
    os.utime(candidate, (100, 100))
    with (
        patch.object(Path, "unlink", side_effect=AssertionError("unlink forbidden")),
        patch("os.remove", side_effect=AssertionError("remove forbidden")),
        patch("os.rmdir", side_effect=AssertionError("rmdir forbidden")),
    ):
        result = inventory(tmp_path, min_free_bytes=0, quota_bytes=1,
                           now=100000, enabled=True)
    assert result["status"] == "OBSERVED"
    assert [entry["name"] for entry in result["candidates"]] == ["old.part"]
    assert candidate.read_bytes() == b"intact"


@pytest.mark.parametrize("failure", [PermissionError("access denied"), OSError("locked directory")])
def test_scan_permission_and_locked_directory_fail_closed(tmp_path, failure):
    """No cleanup or partial manifest if enumeration is inaccessible."""
    (tmp_path / "tmp").mkdir()
    with patch("src.api.guardians.storage_guardian.os.scandir", side_effect=failure):
        result = inventory(tmp_path, min_free_bytes=0, quota_bytes=100,
                           now=100000, enabled=True)
    assert result["status"] == "ALERT_IO_FAILURE"
    assert result["candidates"] == []
    assert result["bytes_reclaimable"] == 0


def test_lstat_permission_failure_does_not_escape(tmp_path):
    folder = tmp_path / "tmp"
    folder.mkdir()
    candidate = folder / "old.part"
    candidate.write_bytes(b"protected")
    os.utime(candidate, (100, 100))
    original = Path.lstat

    def inaccessible(path):
        if path == candidate:
            raise PermissionError("denied")
        return original(path)

    with patch.object(Path, "lstat", inaccessible):
        result = inventory(tmp_path, min_free_bytes=0, quota_bytes=100,
                           now=100000, enabled=True)
    assert result["status"] == "ALERT_REPARSE_ENTRY"
    assert result["candidates"] == []
    assert candidate.read_bytes() == b"protected"


def test_disk_telemetry_failure_rejects_manifest(tmp_path):
    (tmp_path / "tmp").mkdir()
    with patch("src.api.guardians.storage_guardian.shutil.disk_usage",
               side_effect=OSError("disk offline")):
        result = inventory(tmp_path, min_free_bytes=0, quota_bytes=100,
                           enabled=True)
    assert result["status"] == "ALERT_IO_FAILURE"
    assert result["candidates"] == []


def test_full_uninstall_requires_master_owned_adapter(tmp_path):
    """No Storage API may delete even an isolated full TESTER fixture."""
    owned = tmp_path / "TesterLab"
    for directory in ("app", "config", "data", "postgres-data",
                      "logs", "scripts", "install", "tmp"):
        (owned / directory).mkdir(parents=True)
    for relative in ("data/candles.db", "data/results.json",
                     "postgres-data/PG_VERSION", "logs/log.txt",
                     "config/config.json", "install/backup.zip"):
        target = owned / relative
        target.write_bytes(b"retain until MASTER verified uninstall")
    with pytest.raises(PermissionError):
        cleanup_authorization(owned, confirmation="DELETE TESTER", enabled=True)
    for relative in ("data/candles.db", "data/results.json",
                     "postgres-data/PG_VERSION", "logs/log.txt",
                     "config/config.json", "install/backup.zip"):
        assert (owned / relative).exists()


@pytest.mark.parametrize(
    "foreign_name",
    ["backup.zip", "candles.db", "results.json", "pg_wal", ".hidden.part.bak"],
)
def test_non_part_foreign_data_never_becomes_cleanup_candidate(tmp_path, foreign_name):
    """Storage only proposes direct old *.part, never historical data or backups."""
    folder = tmp_path / "tmp"
    folder.mkdir()
    foreign = folder / foreign_name
    foreign.write_bytes(b"foreign")
    os.utime(foreign, (100, 100))
    result = inventory(tmp_path, min_free_bytes=0, quota_bytes=1,
                       now=100000, enabled=True)
    assert result["status"] == "OBSERVED"
    assert result["candidates"] == []
    assert foreign.read_bytes() == b"foreign"


def test_disk_full_proposes_pause_without_mutation(tmp_path):
    """Simulate no available disk space; do not run a download or cleanup."""
    folder = tmp_path / "tmp"
    folder.mkdir()
    old = folder / "old.part"
    old.write_bytes(b"retain")
    os.utime(old, (100, 100))
    with patch("src.api.guardians.storage_guardian.shutil.disk_usage") as disk:
        disk.return_value.free = 0
        disk.return_value.total = 100
        result = inventory(tmp_path, min_free_bytes=1, quota_bytes=1,
                           now=100000, enabled=True)
    assert result["status"] == "OBSERVED"
    assert "PROPOSE_PAUSE_NEW_DOWNLOADS" in result["proposals"]
    assert "ALERT_TMP_QUOTA" in result["proposals"]
    assert old.read_bytes() == b"retain"


def test_mid_scan_file_disappearance_rejects_entire_manifest(tmp_path):
    """A file vanishing after enumeration cannot produce partial cleanup approval."""
    folder = tmp_path / "tmp"
    folder.mkdir()
    old = folder / "old.part"
    old.write_bytes(b"retain")
    os.utime(old, (100, 100))
    original = Path.lstat

    def disappeared(path):
        if path == old:
            raise FileNotFoundError("injected disappearance")
        return original(path)

    with patch.object(Path, "lstat", disappeared):
        result = inventory(tmp_path, min_free_bytes=0, quota_bytes=1,
                           now=100000, enabled=True)
    assert result["status"] in ("ALERT_REPARSE_ENTRY", "ALERT_IO_FAILURE")
    assert result["candidates"] == []
    assert old.read_bytes() == b"retain"


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -1, True, "bad"])
def test_invalid_clock_preempts_missing_root(invalid, tmp_path):
    """Invalid clock is deterministic even when no runtime root is installed."""
    result = inventory(tmp_path / "not-installed", min_free_bytes=0,
                       quota_bytes=1, now=invalid, enabled=True)
    assert result["status"] == "ALERT_INVALID_CLOCK"
    assert result["candidates"] == []


def test_quota_budget_rejects_bool_and_noninteger_before_io():
    with patch("src.api.guardians.storage_guardian.shutil.disk_usage",
               side_effect=AssertionError("disk must not be inspected")):
        assert inventory("nonexistent", min_free_bytes=True,
                         quota_bytes=1, enabled=True)["status"] == "ALERT_INVALID_BUDGET"
        assert inventory("nonexistent", min_free_bytes=0,
                         quota_bytes=1.5, enabled=True)["status"] == "ALERT_INVALID_BUDGET"
