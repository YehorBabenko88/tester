"""Pure/mocked guards; never opens SCM or changes physical installation."""

import hashlib
import json
from pathlib import Path

import pytest

from src.api.physical_runtime import supervise
from src.api.physical_scm import apply
from src.api.physical_scope import (
    ScopeViolation,
    failure_budget,
    image_path,
    owned_service,
    validate_release,
)


def write_owner(root):
    (root / "installation-owner.json").write_text(
        json.dumps(
            dict(
                schema_version=1,
                repository="YehorBabenko88/tester",
                installation_id="00000000-0000-4000-8000-000000000001",
                host_fingerprint="658ac47c38eb17a6",
                root=str(root),
                services=["TesterPhysicalTest20261107", "TesterPhysicalPg20261107"],
                created_at="2026-11-07T00:00:00Z",
            )
        )
    )


def release(tmp_path):
    root = tmp_path / "TesterPhysicalTest20261107"
    folder = root / "releases" / ("a" * 40)
    folder.mkdir(parents=True)
    write_owner(root)
    (folder / "entry.py").write_text("fixture")
    manifest = {
        "schema_version": "1.0",
        "release_sha": "a" * 40,
        "files": {"entry.py": hashlib.sha256(b"fixture").hexdigest()},
    }
    raw = json.dumps(manifest).encode()
    (folder / "physical-manifest.json").write_bytes(raw)
    return root, folder, hashlib.sha256(raw).hexdigest()


def test_manifest_hash_and_inventory(tmp_path):
    root, folder, digest = release(tmp_path)
    assert validate_release(folder, digest, root=root) == folder
    (folder / "entry.py").write_text("tampered")
    with pytest.raises(ScopeViolation):
        validate_release(folder, digest, root=root)


@pytest.mark.parametrize("mutation", ["extra", "escape", "wronghash", "otherrelease"])
def test_manifest_rejects_foreign_payload(tmp_path, mutation):
    root, folder, digest = release(tmp_path)
    if mutation == "extra":
        (folder / "extra.py").write_text("extra")
    if mutation == "escape":
        folder = root.parent
    if mutation == "wronghash":
        digest = "0" * 64
    if mutation == "otherrelease":
        folder = folder.parent / ("b" * 40)
    with pytest.raises((ScopeViolation, TypeError)):
        validate_release(folder, digest, root=root)


def test_never_claim_existing_service_by_name_alone(tmp_path):
    root, folder, digest = release(tmp_path)
    image = image_path(folder, root)
    options = {
        "Owner": "TesterPhysicalTest20261107/v1",
        "InstallationId": "00000000-0000-4000-8000-000000000001",
        "Root": str(root),
        "Release": str(folder),
        "ManifestSHA256": digest,
    }
    assert owned_service("TesterPhysicalTest20261107", image, options, root=root)
    for name, path, opts in [
        ("TesterMarketLab", image, options),
        ("TesterPhysicalTest20261107", "evil.exe", options),
        ("TesterPhysicalTest20261107", image, {}),
    ]:
        with pytest.raises(ScopeViolation):
            owned_service(name, path, opts, root=root)


def test_persistent_three_failure_budget(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    for attempt in range(3):
        assert failure_budget(runtime, record="readiness_timeout") == attempt + 1
    with pytest.raises(ScopeViolation):
        failure_budget(runtime)
    assert json.loads((runtime / "scm-failures.json").read_text())["failures"] == 3


class FakeSCM:
    def __init__(self, existing=None):
        self.existing = existing
        self.mutations = []

    def inspect(self):
        return self.existing

    def install(self, image, options, dependency):
        self.mutations.append(("install", image, options, dependency))

    def update(self, image, options, dependency):
        self.mutations.append(("update", image, options, dependency))

    def start(self):
        self.mutations.append(("start",))

    def stop(self):
        self.mutations.append(("stop",))


def test_install_mocked_scope(tmp_path):
    root, folder, digest = release(tmp_path)
    scm = FakeSCM()
    apply("install", folder, digest, scm, root=root, preflight=lambda *args: None)
    assert scm.mutations[0][0] == "install"
    assert scm.mutations[0][3] is None
    assert scm.mutations[0][2]["Owner"] == "TesterPhysicalTest20261107/v1"


def test_foreign_service_cannot_be_updated_started_stopped(tmp_path):
    root, folder, digest = release(tmp_path)
    for action in ["install", "update", "start", "stop"]:
        scm = FakeSCM(
            {
                "image": "foreign.exe",
                "options": {},
                "state": "stopped",
                "account": "NT AUTHORITY\\LocalService",
            }
        )
        with pytest.raises(ScopeViolation):
            apply(action, folder, digest, scm, root=root, preflight=lambda *args: None)
        assert not scm.mutations


def test_running_update_rejected_and_stop_only_owned(tmp_path):
    root, folder, digest = release(tmp_path)
    options = {
        "Owner": "TesterPhysicalTest20261107/v1",
        "InstallationId": "00000000-0000-4000-8000-000000000001",
        "Root": str(root),
        "Release": str(folder),
        "ManifestSHA256": digest,
    }
    current = {
        "image": image_path(folder, root),
        "options": options,
        "state": "running",
        "account": "NT AUTHORITY\\LocalService",
    }
    scm = FakeSCM(current)
    with pytest.raises(ScopeViolation):
        apply("update", folder, digest, scm, root=root, preflight=lambda *args: None)
    apply("stop", folder, digest, scm, root=root, preflight=lambda *args: None)
    assert scm.mutations == [("stop",)]


class Stop:
    def __init__(self):
        self.value = False

    def is_set(self):
        return self.value

    def set(self):
        self.value = True


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def wait(self, n):
        self.now += n


class Child:
    def __init__(self, stop):
        self.stop = stop
        self.killed = False

    def poll(self):
        return 0 if self.stop.is_set() else None

    def close(self):
        pass

    def kill_tree(self):
        self.killed = True


def test_no_running_status_before_real_worker_readiness(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    stop = Stop()
    clock = Clock()
    states = []
    child = Child(stop)

    def health():
        if clock.now < 2:
            return {
                "status": "degraded",
                "database": "ok",
                "worker": "unavailable",
                "storage": "ok",
            }
        return {"status": "degraded", "database": "ok", "worker": "ok", "storage": "ok"}

    def report(state):
        states.append((state, clock.now))
        if state == "running":
            stop.set()

    supervise(runtime, stop, lambda: child, health, report, clock=clock, wait=clock.wait)
    assert ("running", 2) in states
    assert not child.killed


def test_readiness_timeout_records_failure_and_kills_tree(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    stop = Stop()
    clock = Clock()
    child = Child(stop)
    states = []
    with pytest.raises(RuntimeError):
        supervise(
            runtime,
            stop,
            lambda: child,
            lambda: None,
            states.append,
            clock=clock,
            wait=clock.wait,
            start_timeout=3,
            stop_timeout=2,
        )
    assert child.killed
    assert "running" not in states
    assert json.loads((runtime / "scm-failures.json").read_text())["failures"] == 1


def test_native_recovery_includes_non_crash_failures():
    from types import SimpleNamespace

    from src.api.physical_scm import NativeSCM

    calls = []
    options = []
    scm = NativeSCM.__new__(NativeSCM)
    scm.ws = SimpleNamespace(
        SERVICE_CONFIG_FAILURE_ACTIONS=1,
        SERVICE_CONFIG_FAILURE_ACTIONS_FLAG=4,
        SERVICE_CONFIG_DESCRIPTION=2,
        SC_ACTION_RESTART=1,
        SC_ACTION_NONE=0,
        ChangeServiceConfig2=lambda *args: calls.append(args),
    )
    scm.util = SimpleNamespace(SetServiceCustomOption=lambda *args: options.append(args))
    scm._configure("owned-handle", {"Owner": "TesterPhysicalTest20261107/v1"})
    assert ("owned-handle", 4, True) in calls
    assert calls[0][2]["ResetPeriod"] == 0xFFFFFFFF
    assert calls[0][2]["Actions"] == [(1, 15000), (1, 30000), (0, 0)]
    assert all(item[0] == "TesterPhysicalTest20261107" for item in options)


def test_unconnected_health_explicit_provider_unavailable(tmp_path):
    from fastapi.testclient import TestClient

    from src.api.config import Settings
    from src.api.main import create_app

    c = TestClient(create_app(Settings(data_dir=tmp_path)), base_url="http://127.0.0.1:18765")
    response = c.get("/api/health")
    assert response.status_code == 503
    assert response.json()["data"]["provider"] == "unavailable"


def test_readiness_rejects_other_instance(monkeypatch):
    from types import SimpleNamespace

    import src.api.physical_runtime as runtime
    from src.api.physical_runtime import probe_health

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args):
            pass

        def close(self):
            pass

        def getresponse(self):
            return SimpleNamespace(
                status=200,
                read=lambda n: json.dumps(
                    {
                        "schema_version": "1.0",
                        "data": {
                            "instance_id": "foreign",
                            "database": "ok",
                            "worker": "ok",
                            "storage": "ok",
                        },
                    }
                ).encode(),
            )

    monkeypatch.setattr(runtime.http.client, "HTTPConnection", Connection)
    assert probe_health("owned") is None
    assert probe_health("foreign")["worker"] == "ok"


def test_bootstrap_errors_count_once_and_block_fourth_launch(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    import src.api.physical_host as host

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    write_owner(tmp_path)
    monkeypatch.setattr(host, "ROOT", tmp_path)
    captured = []
    reports = []
    launched = []

    class Framework:
        def __init__(self, args):
            pass

        def ReportServiceStatus(self, status, **kwargs):
            reports.append(status)

    monkeypatch.setitem(
        sys.modules,
        "servicemanager",
        SimpleNamespace(
            Initialize=lambda: None,
            PrepareToHostSingle=captured.append,
            StartServiceCtrlDispatcher=lambda: None,
            LogErrorMsg=lambda s: None,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32service",
        SimpleNamespace(
            SERVICE_START_PENDING=2, SERVICE_RUNNING=4, SERVICE_STOP_PENDING=3, SERVICE_STOPPED=1
        ),
    )
    monkeypatch.setitem(
        sys.modules, "win32serviceutil", SimpleNamespace(ServiceFramework=Framework)
    )
    host.dispatch()
    cls = captured[0]

    def fail(self):
        launched.append(1)
        raise RuntimeError("bootstrap fixture")

    monkeypatch.setattr(cls, "SvcDoRun", fail)
    for _ in range(4):
        instance = cls(["TesterPhysicalTest20261107"])
        with pytest.raises((RuntimeError, ScopeViolation)):
            instance.SvcRun()
    assert len(launched) == 3
    assert json.loads((runtime / "scm-failures.json").read_text())["failures"] == 3
    assert 4 not in reports
    instance.current_status = 2
    instance.SvcInterrogate()
    assert reports[-1] == 2


def test_supervisor_job_is_assigned_before_resume(monkeypatch):
    import sys
    from types import SimpleNamespace

    from src.api.physical_host import JobProcess

    calls = []

    def create_job(attributes, name):
        # pywin32's WCHAR* parameter rejects None even though the Win32 API
        # documents a nullable name. Empty text creates the private unnamed job.
        if type(name) is not str:
            raise TypeError("None is not a valid string in this context")
        assert name == ""
        return "job"

    monkeypatch.setitem(
        sys.modules, "win32api", SimpleNamespace(CloseHandle=lambda h: calls.append(("close", h)))
    )
    monkeypatch.setitem(
        sys.modules, "win32event", SimpleNamespace(WaitForSingleObject=lambda *a: 258)
    )
    monkeypatch.setitem(
        sys.modules,
        "win32job",
        SimpleNamespace(
            CreateJobObject=create_job,
            JobObjectExtendedLimitInformation=9,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE=8192,
            QueryInformationJobObject=lambda *a: {"BasicLimitInformation": {"LimitFlags": 0}},
            SetInformationJobObject=lambda *a: None,
            AssignProcessToJobObject=lambda *a: calls.append(("assign",)),
            TerminateJobObject=lambda *a: calls.append(("kill",)),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(
            STARTUPINFO=lambda: None,
            CreateProcess=lambda *a: ("process", "thread", 1, 2),
            ResumeThread=lambda *a: calls.append(("resume",)),
            TerminateProcess=lambda *a: None,
        ),
    )
    child = JobProcess(Path("fixture"))
    assert calls[:2] == [("assign",), ("resume",)]
    child.kill_tree()
    child.close()
    assert ("close", "job") in calls


def test_owner_marker_required_and_linked(tmp_path):
    from src.api.physical_scope import validate_owner

    root, folder, digest = release(tmp_path)
    marker = root / "installation-owner.json"
    marker.write_text("{}")
    with pytest.raises(ScopeViolation):
        validate_owner(root)


def test_config_scope_is_explicit_and_env_does_not_activate_factory(tmp_path, monkeypatch):
    from src.api.physical_scm import preflight

    root, folder, digest = release(tmp_path)
    (root / "venv/Scripts").mkdir(parents=True)
    (root / "venv/Scripts/python.exe").write_text("not executed fixture")
    (root / "runtime").mkdir()
    config = dict(
        database_url="postgresql://fixture@127.0.0.1:55469/tester_physical",
        host="127.0.0.1",
        port=18765,
        data_dir=str(root / "runtime"),
        frontend_dir=str(folder / "frontend"),
    )
    (root / "config.json").write_text(json.dumps(config))
    monkeypatch.setenv("TESTER_PROVIDER_FACTORY", "foreign.module:factory")
    assert preflight(folder, root).provider_factory is None
    config["port"] = 8765
    (root / "config.json").write_text(json.dumps(config))
    with pytest.raises(ScopeViolation):
        preflight(folder, root)


def test_forced_shutdown_is_failure_not_graceful_success(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    stop = Stop()
    clock = Clock()

    class Stuck(Child):
        def poll(self):
            return None

    child = Stuck(stop)

    def report(state):
        if state == "running":
            stop.set()

    with pytest.raises(RuntimeError, match="shutdown_timeout"):
        supervise(
            runtime,
            stop,
            lambda: child,
            lambda: {"database": "ok", "worker": "ok", "storage": "ok"},
            report,
            clock=clock,
            wait=clock.wait,
            stop_timeout=2,
        )
    assert child.killed
    assert (
        json.loads((runtime / "scm-failures.json").read_text())["last_signature"]
        == "shutdown_timeout"
    )
