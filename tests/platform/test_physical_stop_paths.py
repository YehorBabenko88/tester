"""Pure stop/path races; no native service, process, junction or ACL operations."""

import stat
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.api.physical_runtime import supervise
from src.api.physical_scope import ScopeViolation, plain_path


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def wait(self, duration):
        self.now += duration


def test_cancelled_start_never_spawns_or_records_failure(tmp_path):
    stop = threading.Event()
    stop.set()
    spawned, reported = [], []
    supervise(tmp_path, stop, lambda: spawned.append(True), lambda: None, reported.append)
    assert spawned == []
    assert reported == []
    assert not (tmp_path / "stop.request").exists()
    assert not (tmp_path / "scm-failures.json").exists()


def test_stop_signal_is_reasserted_after_supervisor_startup_unlink(tmp_path):
    stop = threading.Event()
    clock = Clock()
    marker = tmp_path / "stop.request"

    class Child:
        cleared = False
        killed = False
        closed = False

        def poll(self):
            if not self.cleared and marker.exists():
                # Exact race: supervisor.run clears the old marker after its DB setup.
                marker.unlink()
                self.cleared = True
                return None
            return 0 if self.cleared and marker.exists() else None

        def kill_tree(self):
            self.killed = True

        def close(self):
            self.closed = True

    child = Child()

    def spawn():
        stop.set()  # SCM cancellation arrives just after process creation.
        return child

    supervise(
        tmp_path,
        stop,
        spawn,
        lambda: None,
        lambda state: None,
        clock=clock,
        wait=clock.wait,
        stop_timeout=3,
    )
    assert child.cleared and child.closed
    assert not child.killed
    assert not (tmp_path / "scm-failures.json").exists()


def test_normal_start_still_allows_supervisor_to_clear_stale_stop(tmp_path):
    stop = threading.Event()
    marker = tmp_path / "stop.request"
    marker.touch()
    states = []

    class Child:
        def poll(self):
            return 0 if stop.is_set() else None

        def close(self):
            pass

        def kill_tree(self):
            pytest.fail("Normal stop must be graceful")

    def spawn():
        assert marker.exists()
        marker.unlink()  # Existing supervisor normal-start behavior is preserved.
        return Child()

    def report(state):
        states.append(state)
        if state == "running":
            stop.set()

    supervise(
        tmp_path,
        stop,
        spawn,
        lambda: dict(database="ok", worker="ok", storage="ok"),
        report,
    )
    assert "running" in states
    assert marker.exists()
    assert not (tmp_path / "scm-failures.json").exists()


def test_junction_reparse_metadata_is_checked_without_following_target(tmp_path, monkeypatch):
    junction = tmp_path / "junction-fixture"
    junction.mkdir()
    original = Path.lstat

    def lstat(path, *args, **kwargs):
        if path == junction:
            # Junction lstat has reparse metadata; following stat sees plain target dir.
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_nlink=1, st_file_attributes=0x410)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ScopeViolation, match="Reparse"):
        plain_path(junction / "missing-child")
