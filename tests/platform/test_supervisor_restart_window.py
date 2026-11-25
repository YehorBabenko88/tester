"""Supervisor: counted crash restarts only, and R5 transient-authority tolerance."""

import threading
import time
import types

import pytest

from src.api import supervisor
from src.api.restart_budget import KILL_SWITCH_EXIT, BudgetDenied, BudgetUnavailable


class Child:
    def __init__(self, exits_after=None):
        self.exit_at = None if exits_after is None else time.monotonic() + exits_after

    def poll(self):
        return 1 if self.exit_at is not None and time.monotonic() >= self.exit_at else None

    def wait(self, timeout=None):
        return 0


class Budget:
    def __init__(self, directory, outage=(), deny_counts=False):
        self.directory = directory
        self.calls = []
        self.outage = list(outage)  # Per-call script for counts()/admit(): True = unavailable.
        self.deny_counts = deny_counts

    def _maybe_unavailable(self):
        if self.outage and self.outage.pop(0):
            raise BudgetUnavailable("PostgreSQL restarting")

    def counts(self):
        self.calls.append("counts")
        if self.deny_counts and len(self.calls) > 1:
            raise BudgetDenied("exhausted or disabled")
        self._maybe_unavailable()
        return {"host": 0, "api": 0, "worker": 0}

    def admit(self, role):
        self.calls.append(("restart", role))
        self._maybe_unavailable()

    def admit_initial(self, role):
        self.calls.append(("initial", role))


def _harness(monkeypatch, tmp_path, budget, children):
    settings = types.SimpleNamespace(
        data_dir=tmp_path, dsn="postgresql://unused", lease_seconds=30, max_pending=1, max_running=1
    )
    monkeypatch.setattr(supervisor, "load_settings", lambda: settings)
    monkeypatch.setattr(
        supervisor,
        "configure",
        lambda *a: types.SimpleNamespace(
            info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None
        ),
    )

    class Queue:
        def __init__(self, *a):
            pass

        def configure_limits(self, *a):
            pass

        def set_draining(self, value):
            pass

    monkeypatch.setattr(supervisor, "JobQueue", Queue)
    monkeypatch.setattr(
        supervisor.DatabaseRestartBudget, "from_environment", classmethod(lambda cls, env: budget)
    )
    spawned = []

    def popen(argv, **kwargs):
        spawned.append(argv[-1])
        return children.pop(0) if children else Child()

    monkeypatch.setattr(supervisor.subprocess, "Popen", popen)
    return spawned


def _run_for(seconds, tmp_path):
    result = {}
    stopper = threading.Timer(seconds, lambda: (tmp_path / "stop.request").touch())
    stopper.start()
    try:
        result["code"] = supervisor.run()
    finally:
        stopper.cancel()
    return result["code"]


def test_initial_launches_are_not_counted_restarts_are(monkeypatch, tmp_path):
    budget = Budget(tmp_path)
    spawned = _harness(monkeypatch, tmp_path, budget, [Child(), Child(exits_after=0.3)])
    assert _run_for(3.5, tmp_path) == 0
    launches = [c for c in budget.calls if c != "counts"]
    assert launches == [("initial", "api"), ("initial", "worker"), ("restart", "worker")]
    assert spawned == ["src.api.main", "src.api.worker", "src.api.worker"]


def test_postgres_blip_keeps_children_and_does_not_exit(monkeypatch, tmp_path):
    # Before R5 the first periodic BudgetUnavailable drained children and exited 1,
    # which the SCM answered with a host restart.
    budget = Budget(tmp_path, outage=[False, True, True, True])
    spawned = _harness(monkeypatch, tmp_path, budget, [])
    assert _run_for(5, tmp_path) == 0
    assert spawned == ["src.api.main", "src.api.worker"]


def test_restart_waits_for_authority_never_spawns_unreserved(monkeypatch, tmp_path):
    # counts(): ok at startup; restart admission unavailable twice, then granted.
    budget = Budget(tmp_path)
    spawned = _harness(monkeypatch, tmp_path, budget, [Child(), Child(exits_after=0.3)])
    failures = [True, True]

    def admit(role):
        budget.calls.append(("restart", role))
        if failures:
            failures.pop()
            raise BudgetUnavailable("PostgreSQL restarting")
        return None

    budget.admit = admit
    assert _run_for(5, tmp_path) == 0
    assert spawned == ["src.api.main", "src.api.worker", "src.api.worker"]
    assert budget.calls.count(("restart", "worker")) == 3


def test_outage_beyond_grace_drains_and_exits(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor, "AUTHORITY_GRACE_SECONDS", 0.5)
    budget = Budget(tmp_path, outage=[False] + [True] * 100)
    _harness(monkeypatch, tmp_path, budget, [])
    assert _run_for(8, tmp_path) == 1


def test_denied_policy_still_drains_immediately(monkeypatch, tmp_path):
    budget = Budget(tmp_path, deny_counts=True)
    _harness(monkeypatch, tmp_path, budget, [])
    started = time.monotonic()
    # counts() never fails for an exhausted window: a definite denial is the owner's
    # SQL kill switch, reported distinctly (before: 1, recorded as a host crash).
    assert _run_for(8, tmp_path) == KILL_SWITCH_EXIT
    assert time.monotonic() - started < 6


def test_runtime_kill_switch_file_drain_reports_kill_switch_exit(monkeypatch, tmp_path):
    budget = Budget(tmp_path)
    _harness(monkeypatch, tmp_path, budget, [])
    threading.Timer(0.5, lambda: (tmp_path / "restart.disabled").touch()).start()
    started = time.monotonic()
    assert _run_for(8, tmp_path) == KILL_SWITCH_EXIT
    assert time.monotonic() - started < 6


def test_exhausted_child_window_stays_a_failure_exit(monkeypatch, tmp_path):
    budget = Budget(tmp_path)
    spawned = _harness(monkeypatch, tmp_path, budget, [Child(), Child(exits_after=0.3)])

    def admit(role):
        budget.calls.append(("restart", role))
        raise BudgetDenied("restart reservation denied")  # Window exhausted.

    budget.admit = admit
    assert _run_for(8, tmp_path) == 1
    assert spawned == ["src.api.main", "src.api.worker"]


def test_launch_component_rejects_unknown_role():
    with pytest.raises(ValueError):
        supervisor.launch_component("shell")
