"""Issue #106/R4/R5: SCM shutdown handling, PostgreSQL readiness bound, admission retry.

pywin32 is replaced by a minimal stand-in that mirrors ServiceFramework's real
accepted-controls rule (SHUTDOWN only when SvcShutdown exists) and its
ServiceCtrlHandlerEx dispatch, so these run on every platform.
"""

import importlib.util
import sys
import types
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STOP, SHUTDOWN, PRESHUTDOWN = 1, 5, 0x0F
ACCEPT_STOP, ACCEPT_SHUTDOWN, ACCEPT_PRESHUTDOWN = 1, 4, 0x100


class FakeFramework:
    def __init__(self, args):
        self.reports = []

    def GetAcceptedControls(self):
        accepted = ACCEPT_STOP if hasattr(self, "SvcStop") else 0
        if hasattr(self, "SvcShutdown"):
            accepted |= ACCEPT_SHUTDOWN
        return accepted

    def ServiceCtrlHandlerEx(self, control, event_type, data):
        if control == STOP:
            return self.SvcStop()
        if control == SHUTDOWN:
            return self.SvcShutdown()
        return self.SvcOtherEx(control, event_type, data)

    def SvcOtherEx(self, control, event_type, data):
        return None

    def ReportServiceStatus(self, status, **kwargs):
        self.reports.append((status, kwargs))


@pytest.fixture
def lab_service(monkeypatch):
    events = []
    win32event = types.SimpleNamespace(
        CreateEvent=lambda *a: "event",
        SetEvent=lambda event: events.append(event),
        WaitForSingleObject=lambda *a: 1,
        WAIT_OBJECT_0=0,
    )
    for name, module in {
        "win32serviceutil": types.SimpleNamespace(ServiceFramework=FakeFramework),
        "win32event": win32event,
        "win32service": types.SimpleNamespace(SERVICE_STOP_PENDING=3, SERVICE_START_PENDING=2),
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    # Private copy: the shared src.api.lab_service keeps its real (absent) pywin32 base.
    spec = importlib.util.spec_from_file_location(
        "src.api._lab_service_shutdown_probe", ROOT / "src/api/lab_service.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, events


def test_service_accepts_shutdown_and_preshutdown(lab_service):
    module, _ = lab_service
    service = module.LabService(["TesterReleaseApp_x"])
    accepted = service.GetAcceptedControls()
    # Before #106 no SvcShutdown existed, so SHUTDOWN was not accepted at all.
    assert accepted & ACCEPT_STOP and accepted & ACCEPT_SHUTDOWN
    assert accepted & ACCEPT_PRESHUTDOWN


@pytest.mark.parametrize("control", [SHUTDOWN, PRESHUTDOWN])
def test_os_shutdown_runs_the_same_graceful_stop(lab_service, tmp_path, control):
    module, events = lab_service
    service = module.LabService(["TesterReleaseApp_x"])
    service.root = tmp_path
    (tmp_path / "runtime").mkdir()
    assert service.ServiceCtrlHandlerEx(control, 0, None) in (None, 0)
    assert service.shutdown_requested is True
    assert events == ["event"]  # SvcDoRun's stop path: supervisor drain / pg_ctl fast stop.
    assert (tmp_path / "runtime/stop.request").exists()
    assert service.reports[0][0] == 3 and service.reports[0][1]["waitHint"] >= 30000


def test_unknown_controls_still_reach_framework(lab_service):
    module, events = lab_service
    service = module.LabService(["TesterReleaseApp_x"])
    assert service.ServiceCtrlHandlerEx(0x80, 0, None) is None
    assert events == [] and service.shutdown_requested is False


def test_clean_stop_record_failure_is_best_effort(lab_service):
    module, _ = lab_service
    service = module.LabService(["TesterReleaseApp_x"])
    phases = []
    service.record_phase = phases.append

    def unavailable(root):
        raise OSError("PostgreSQL already stopped during shutdown")

    service.record_clean_stop = unavailable
    service._mark_clean_stop(Path("."))
    service._mark_clean_stop(Path("."))  # Recorded at most once per host run.
    assert phases == ["clean-stop-unrecorded"]


def test_postgres_readiness_allows_crash_recovery_app_stays_bounded():
    from src.api import release_service as rs
    from src.api.lab_service import LabService

    source = (ROOT / "src/api/lab_service.py").read_text(encoding="utf-8")
    assert "time.monotonic() + 55" not in source
    assert "time.monotonic() + self.startup_deadline_seconds" in source
    assert LabService.startup_deadline_seconds == 55
    assert rs.ReleaseService.startup_deadline_seconds == 55
    assert rs.ReleasePostgresService.startup_deadline_seconds >= 300
    assert rs.PG_START_SECONDS > rs.ReleasePostgresService.startup_deadline_seconds


def _fake_budget(monkeypatch, behaviour):
    calls = []

    class Denied(ValueError):
        pass

    class Unavailable(Denied):
        pass

    class Budget:
        @classmethod
        def from_config(cls, *args):
            return cls()

        def admit(self, role):
            calls.append(role)
            behaviour(len(calls), Denied, Unavailable)

        def record_clean_stop(self):
            calls.append("clean-stop")

    monkeypatch.setitem(
        sys.modules,
        "src.api.restart_budget",
        types.SimpleNamespace(
            DatabaseRestartBudget=Budget, BudgetDenied=Denied, BudgetUnavailable=Unavailable
        ),
    )
    return calls, Unavailable


def _release_service(monkeypatch, tmp_path):
    from src.api import release_service as rs

    marker = dict(installation_id=str(uuid.uuid4()), source_sha="a" * 40)
    monkeypatch.setattr(rs, "load_marker", lambda root: marker)
    service = object.__new__(rs.ReleaseService)
    monkeypatch.setattr(service, "_admission_pause", lambda: False)
    return service


def test_transient_authority_outage_retries_instead_of_scm_failure(monkeypatch, tmp_path):
    def behaviour(count, denied, unavailable):
        if count < 3:
            raise unavailable("PostgreSQL still recovering")

    calls, _ = _fake_budget(monkeypatch, behaviour)
    service = _release_service(monkeypatch, tmp_path)
    assert service.admit_start(tmp_path) is True
    service.record_clean_stop(tmp_path)
    assert calls == ["host", "host", "host", "clean-stop"]


def test_persistent_outage_stays_distinct_from_exhausted_policy(monkeypatch, tmp_path):
    def behaviour(count, denied, unavailable):
        raise unavailable("PostgreSQL down")

    calls, unavailable = _fake_budget(monkeypatch, behaviour)
    service = _release_service(monkeypatch, tmp_path)
    service.admission_grace_seconds = 0
    with pytest.raises(unavailable):
        service.admit_start(tmp_path)
    assert calls == ["host"]
    assert getattr(service, "_restart_budget", None) is None


def test_postgres_and_provisioning_hosts_record_no_restart_marker(tmp_path):
    from src.api import release_service as rs

    service = object.__new__(rs.ReleasePostgresService)
    assert service.admit_start(tmp_path) is True
    service.record_clean_stop(tmp_path)  # No budget object: no-op, no SQL.


def test_operator_start_waits_while_postgres_recovery_is_pending(monkeypatch):
    from src.api import release_service as rs
    from src.api.lab_install import LabViolation

    states = iter(["pending", "pending"])
    waits = []

    class SCM:
        def wait(self, name, state):
            waits.append(name)
            if len(waits) < 3:
                raise LabViolation("SCM transition timed out")

        def inspect(self, name):
            return dict(state=next(states))

    controller = object.__new__(rs.NativeController)
    controller.scm = SCM()
    controller._wait_running("pg", 600)
    assert waits == ["pg", "pg", "pg"]

    class Stopped(SCM):
        def inspect(self, name):
            return dict(state="stopped")

    controller.scm = Stopped()
    waits.clear()
    with pytest.raises(LabViolation):
        controller._wait_running("app", 600)  # A failed (stopped) start is not waited out.
