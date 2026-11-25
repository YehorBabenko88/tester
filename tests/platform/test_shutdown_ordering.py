"""Routine Windows reboots must never exhaust the host crash-restart window.

Windows sends PRESHUTDOWN to the App host and the PostgreSQL host concurrently and
does not honour the service dependency. Before this fix the PostgreSQL host ran
``pg_ctl -m fast stop`` immediately, so it could stop the cluster before the App host
recorded its clean stop; the next boot was then counted as a crash and three quick
reboots denied the fourth boot. A supervisor drain caused by an owner kill switch was
not recorded as a clean stop either.

The SCM and PostgreSQL are replaced by injected probes; the real host methods
(``ReleaseService.record_clean_stop``/``policy_drain``,
``ReleasePostgresService.await_dependent_stop``/``graceful_stop``,
``LabService._child_exit_ends_run``) and the real ``DatabaseRestartBudget``
admission logic run against an in-memory model of migration 008's host semantics.
"""

import hashlib
import threading
import time
import uuid

import pytest

from src.api import release_service as rs
from src.api.restart_budget import (
    KILL_SWITCH_EXIT,
    LIMITS,
    BudgetDenied,
    BudgetUnavailable,
    DatabaseRestartBudget,
)

MARKER = dict(
    installation_id=str(uuid.uuid4()),
    source_sha="a" * 40,
    services=dict(app="TesterReleaseApp_x", postgres="TesterReleasePostgres_x"),
)


class Authority:
    """Model of 008 host/host-stop semantics behind a stoppable PostgreSQL."""

    def __init__(self):
        self.lock = threading.Lock()
        self.up = True
        self.flaky = 0  # Next N requests fail as if PostgreSQL were restarting.
        self.disabled = False
        self.digest = None
        self.crashes = []
        self.requests = []

    def request(self, role):
        with self.lock:
            self.requests.append(role)
            if not self.up or self.flaky:
                self.flaky = max(0, self.flaky - 1)
                raise BudgetUnavailable("connection refused")
            kind, _, token = (role or "counts").partition(":")
            if self.disabled and kind != "host-stop":
                raise BudgetDenied("restart reservation denied")
            if kind == "counts":
                return {"host": len(self.crashes), "api": 0, "worker": 0}
            digest = hashlib.sha256(token.encode()).hexdigest()
            if kind == "host-stop":
                if self.digest != digest:
                    raise BudgetDenied("restart reservation denied")
                self.digest = None
            elif kind == "host":
                if self.digest == digest:
                    return None
                if self.digest is not None:
                    if len(self.crashes) >= LIMITS["host"]:
                        raise BudgetDenied("restart reservation denied")
                    self.crashes.append(time.monotonic())
                self.digest = digest
            return None


class Budget(DatabaseRestartBudget):
    """Real admit/record/kill-switch logic; only the SQL transport is replaced."""

    def __init__(self, authority, directory):
        self.authority = authority
        self.directory = directory
        self.installation_id = MARKER["installation_id"]
        self.source_sha = MARKER["source_sha"]
        self.host_token = None

    def _request(self, role=None):
        clean_stop = role is not None and role.startswith("host-stop:")
        if not clean_stop and (self.directory / "restart.disabled").exists():
            raise BudgetDenied("Restarts explicitly disabled")
        return self.authority.request(role)


@pytest.fixture(autouse=True)
def _marker(monkeypatch):
    monkeypatch.setattr(rs, "load_marker", lambda root: MARKER)


def _app(authority, tmp_path):
    app = object.__new__(rs.ReleaseService)
    app.phases = []
    app.record_phase = app.phases.append
    app._stop_retry_pause = lambda: time.sleep(0.01)
    budget = Budget(authority, tmp_path)
    budget.admit("host")  # Boot: the host start reservation of this run.
    app._restart_budget = budget
    return app


def _pg(app_state, wait_seconds=5.0):
    pg = object.__new__(rs.ReleasePostgresService)
    pg.phases = []
    pg.record_phase = pg.phases.append
    pg._stop_retry_pause = lambda: time.sleep(0.01)
    pg.dependent_stop_wait_seconds = wait_seconds
    pg.dependent_state = lambda marker: app_state["state"]
    return pg


def _reboot(authority, tmp_path, *, record_delay=0.0, drain=0.05, wait=5.0, app_dies=False):
    """One OS shutdown: both hosts get PRESHUTDOWN at the same instant."""
    app = _app(authority, tmp_path)
    app_state = {"state": "active"}
    pg = _pg(app_state, wait)
    go = threading.Barrier(2)

    def app_host():
        go.wait()
        time.sleep(record_delay)  # Slow App service thread (e.g. mid health probe).
        if app_dies:
            app_state["state"] = "stopped"  # Process killed: nothing recorded.
            return
        app._mark_clean_stop(tmp_path)  # LabService's first action on the stop path.
        time.sleep(drain)  # Supervisor drain while PostgreSQL must stay up.
        app_state["state"] = "stopped"

    def pg_host():
        go.wait()
        pg.await_dependent_stop(tmp_path)
        authority.up = False  # pg_ctl -m fast stop.

    threads = [threading.Thread(target=app_host), threading.Thread(target=pg_host)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    authority.up = True  # Next boot: PostgreSQL starts first again.
    return app, pg


def test_many_quick_orderly_reboots_are_never_counted(tmp_path):
    authority = Authority()
    for _ in range(6):  # Twice the hourly host limit.
        app, pg = _reboot(authority, tmp_path, record_delay=0.05, drain=0.1)
        assert "clean-stop-unrecorded" not in app.phases
        assert pg.phases[-1] == "dependent-stopped"
    Budget(authority, tmp_path).admit("host")  # The seventh boot is admitted.
    assert authority.crashes == []


def test_without_the_ordering_wait_postgres_wins_the_race(tmp_path):
    """Reproduces the defect: no wait (old behaviour) makes the reboot a crash."""
    authority = Authority()
    app, pg = _reboot(authority, tmp_path, record_delay=0.1, wait=0)
    assert "clean-stop-unrecorded" in app.phases
    assert pg.phases == ["dependent-stop-timeout"]
    Budget(authority, tmp_path).admit("host")
    assert len(authority.crashes) == 1


def test_slow_postgres_transient_outage_is_retried(tmp_path):
    authority = Authority()
    app = _app(authority, tmp_path)
    authority.flaky = 3  # PostgreSQL slow to answer / briefly restarting.
    app._mark_clean_stop(tmp_path)
    assert "clean-stop-unrecorded" not in app.phases
    assert authority.digest is None
    stops = authority.requests[-4:]
    assert len(set(stops)) == 1 and stops[0].startswith("host-stop:")  # Same token retried.
    Budget(authority, tmp_path).admit("host")
    assert authority.crashes == []


def test_record_retry_is_bounded_when_postgres_is_gone(tmp_path):
    authority = Authority()
    app = _app(authority, tmp_path)
    app.clean_stop_retry_seconds = 0.2
    authority.up = False
    started = time.monotonic()
    app._mark_clean_stop(tmp_path)
    assert time.monotonic() - started < 2
    assert app.phases == ["clean-stop-unrecorded"]


def test_app_drain_slower_than_its_record_keeps_postgres_up(tmp_path):
    authority = Authority()
    app, pg = _reboot(authority, tmp_path, record_delay=0.2, drain=0.4)
    assert pg.phases == ["awaiting-dependent-stop", "dependent-stopped"]
    assert "clean-stop-unrecorded" not in app.phases
    Budget(authority, tmp_path).admit("host")
    assert authority.crashes == []


def test_hung_app_cannot_hold_postgres_beyond_the_bound(tmp_path):
    app_state = {"state": "active"}
    pg = _pg(app_state, wait_seconds=0.2)
    started = time.monotonic()
    assert pg.await_dependent_stop(tmp_path) == "stop-timeout"
    assert time.monotonic() - started < 2
    assert pg.phases == ["awaiting-dependent-stop", "dependent-stop-timeout"]


@pytest.mark.parametrize("state", ["absent", "unknown"])
def test_unobservable_or_absent_app_does_not_delay_shutdown(tmp_path, state):
    pg = _pg({"state": state})
    assert pg.await_dependent_stop(tmp_path) == state
    assert pg.phases == ["dependent-" + state]


def test_genuine_crash_is_still_counted(tmp_path):
    authority = Authority()
    _reboot(authority, tmp_path, app_dies=True)
    Budget(authority, tmp_path).admit("host")
    assert len(authority.crashes) == 1


def test_crash_window_still_exhausts_after_three_real_crashes(tmp_path):
    authority = Authority()
    for _ in range(LIMITS["host"] + 1):  # The first boot has no previous run.
        _reboot(authority, tmp_path, app_dies=True)
    assert len(authority.crashes) == LIMITS["host"]
    with pytest.raises(BudgetDenied):
        _reboot(authority, tmp_path)  # Fourth crash restart: exhausted policy.


def test_graceful_stop_runs_pg_ctl_only_after_app_stopped(monkeypatch, tmp_path):
    order = []
    app_state = {"state": "active"}
    pg = _pg(app_state)
    probes = iter(["active", "active", "stopped"])

    def probe(marker):
        state = next(probes)
        order.append("app-" + state)
        return state

    pg.dependent_state = probe
    monkeypatch.setattr(rs, "preflight", lambda root, **kw: order.append("preflight"))

    class Done:
        returncode = 0

    def run(argv, **kwargs):
        order.append("pg_ctl-" + argv[-1])
        return Done()

    monkeypatch.setattr(rs.subprocess, "run", run)
    pg.graceful_stop(tmp_path)
    assert order == ["preflight", "app-active", "app-active", "app-stopped", "pg_ctl-stop"]


def test_preshutdown_budget_fits_postgres_timeout():
    # Wait + pg_ctl (-t 25, 28 s process bound) must stay inside PostgreSQL's 90 s.
    assert rs.ReleasePostgresService.dependent_stop_wait_seconds + 28 < 90
    # The App host's record retries stay well inside the PostgreSQL wait.
    assert rs.ReleaseService.clean_stop_retry_seconds < (
        rs.ReleasePostgresService.dependent_stop_wait_seconds
    )


# --- Kill-switch drains ---------------------------------------------------------


def _drained(authority, tmp_path, exit_code):
    app = _app(authority, tmp_path)
    app.accepts_child_exit = lambda code: False
    return app, app._child_exit_ends_run(tmp_path, exit_code)


def test_sql_kill_switch_drain_is_a_clean_stop(tmp_path):
    authority = Authority()
    app = _app(authority, tmp_path)
    authority.disabled = True
    assert app._child_exit_ends_run(tmp_path, KILL_SWITCH_EXIT) is True
    assert app.phases == ["drained-by-kill-switch"]
    assert authority.digest is None  # Run ended cleanly under the switch.
    authority.disabled = False
    Budget(authority, tmp_path).admit("host")
    assert authority.crashes == []


def test_file_kill_switch_drain_is_a_clean_stop(tmp_path):
    authority = Authority()
    app = _app(authority, tmp_path)
    (tmp_path / "restart.disabled").touch()
    assert app._child_exit_ends_run(tmp_path, KILL_SWITCH_EXIT) is True
    (tmp_path / "restart.disabled").unlink()
    Budget(authority, tmp_path).admit("host")
    assert authority.crashes == []


def test_forged_kill_switch_status_without_switch_is_a_crash(tmp_path):
    authority = Authority()
    app, ended = _drained(authority, tmp_path, KILL_SWITCH_EXIT)
    assert ended is False and app.phases == []
    assert authority.digest is not None  # Run left open: next start is counted.
    Budget(authority, tmp_path).admit("host")
    assert len(authority.crashes) == 1


def test_ordinary_failure_exit_under_switch_is_not_a_drain(tmp_path):
    authority = Authority()
    app = _app(authority, tmp_path)
    app.accepts_child_exit = lambda code: False
    authority.disabled = True
    assert app._child_exit_ends_run(tmp_path, 1) is False
    assert authority.digest is not None


def test_unavailable_authority_cannot_prove_a_kill_switch(tmp_path):
    authority = Authority()
    app = _app(authority, tmp_path)
    authority.up = False
    assert app.policy_drain(tmp_path, KILL_SWITCH_EXIT) is False


def test_postgres_host_never_treats_child_exit_as_drain(tmp_path):
    pg = object.__new__(rs.ReleasePostgresService)
    assert pg.policy_drain(tmp_path, KILL_SWITCH_EXIT) is False


def test_kill_switch_engaged_distinguishes_denial_from_outage(tmp_path):
    authority = Authority()
    budget = Budget(authority, tmp_path)
    assert budget.kill_switch_engaged() is False
    authority.disabled = True
    assert budget.kill_switch_engaged() is True
    authority.disabled, authority.up = False, False
    assert budget.kill_switch_engaged() is False
    (tmp_path / "restart.disabled").touch()
    assert budget.kill_switch_engaged() is True
