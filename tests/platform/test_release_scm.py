import uuid

from src.api import release_service as rs


def test_release_plan_uses_private_uuid_services_and_auto_start(tmp_path):
    ident = uuid.uuid4()
    marker = dict(
        root=str(tmp_path),
        installation_id=str(ident),
        source_sha="a" * 40,
        services={
            "app": "TesterReleaseApp_" + ident.hex,
            "postgres": "TesterReleasePG_" + ident.hex,
        },
        ports={"api": 18767, "postgres": 55471},
    )
    app, pg = rs.plans(marker)
    assert app["auto_start"] is True and pg["auto_start"] is True
    assert pg["name"] == app["dependency"]
    assert str(tmp_path / "dependencies/postgres/bin/pg_ctl.exe") in pg["image"]
    assert app["recovery_delays_ms"] == [1000, 5000, 0]


def test_release_health_accepts_operational_degraded_provider():
    class Response:
        status = 503

    health = dict(instance_id="owned", database="ok", worker="ok", storage="ok")
    assert rs.ReleaseService.health_ready(None, Response(), health, "owned")
    assert not rs.ReleaseService.health_ready(None, Response(), health, "foreign")
    health["worker"] = "unavailable"
    assert not rs.ReleaseService.health_ready(None, Response(), health, "owned")


def test_real_native_scm_uuid_lifecycle_recovery(tmp_path):
    """Disposable real SCM acceptance; never uses canonical C:\\TesterLab."""
    import ctypes
    import json
    import os
    import shutil
    import subprocess
    import sys
    import time
    from pathlib import Path

    import pytest

    required = os.environ.get("TESTER_REQUIRE_NATIVE_SCM") == "1"
    if os.name != "nt":
        if required:
            pytest.fail("Native SCM acceptance requires Windows")
        pytest.skip("Real Windows SCM requires Windows")
    if not ctypes.windll.shell32.IsUserAnAdmin():
        if required:
            pytest.fail("Native SCM acceptance requires elevated disposable runner")
        pytest.skip("Real Windows SCM requires elevated disposable runner")
    import winreg

    import win32api
    import win32con
    import win32service

    from src.api.lab_scm import NativeSCM, owned

    root = tmp_path / ("TesterReleaseTest_" + uuid.uuid4().hex)
    host = root / "dependencies/service"
    host.mkdir(parents=True)
    base = Path(sys.base_prefix)
    # Private, complete CPython runtime: no existing installation is modified.
    shutil.copytree(
        base,
        host,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            "site-packages", "__pycache__", "Scripts", "Doc", "tcl", "Tools"
        ),
    )
    site = Path(sys.prefix) / "Lib/site-packages"
    shutil.copy2(site / "win32/pythonservice.exe", host / "pythonservice.exe")
    shutil.copy2(site / "win32/servicemanager.pyd", host / "servicemanager.pyd")
    for dll in (site / "pywin32_system32").glob("*.dll"):
        shutil.copy2(dll, host / dll.name)
    (host / "Lib/site-packages").mkdir(exist_ok=True)
    shutil.copytree(site / "win32", host / "Lib/site-packages/win32")
    (host / "Lib/sitecustomize.py").write_text("import sys; sys.dont_write_bytecode=True")
    (host / "Lib/site-packages/tester.pth").write_text(
        "\n".join(
            map(str, [host, host / "Lib/site-packages/win32", host / "Lib/site-packages/win32/lib"])
        )
    )
    (host / "native_fixture.py").write_text("""import win32serviceutil,win32service,win32event
class Fixture(win32serviceutil.ServiceFramework):
    _svc_name_="TesterDisposableFixture"
    def __init__(self,args):
        self._svc_name_=args[0]
        super().__init__(args)
        self.stop=win32event.CreateEvent(None,True,False,None)
    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop)
    def SvcDoRun(self):
        win32event.WaitForSingleObject(self.stop,win32event.INFINITE)
""")
    ident = uuid.uuid4()
    plan = dict(
        name="TesterReleaseApp_" + ident.hex,
        root=str(root),
        installation_id=str(ident),
        source_sha="a" * 40,
        account="NT SERVICE\\TesterReleaseApp_" + ident.hex,
        service_sid=True,
        auto_start=True,
        recovery_delays_ms=[1000, 5000, 0],
        python_class=str(host / "native_fixture") + ".Fixture",
        release=str(host),
    )
    image = subprocess.list2cmdline([str(host / "pythonservice.exe")])
    scm = NativeSCM(mutating=True)
    proof = []
    original_failure = None
    try:
        assert scm.inspect(plan["name"]) is None
        scm.install(plan, image, "")
        # NativeSCM writes PythonClass for dependent app services. Fixture dependency
        # is intentionally absent; install its same owned PythonClass explicitly.
        with winreg.CreateKey(
            winreg.HKEY_LOCAL_MACHINE,
            "SYSTEM\\CurrentControlSet\\Services\\" + plan["name"] + "\\PythonClass",
        ) as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, plan["python_class"])
        import win32security

        sid, _, _ = win32security.LookupAccountName(None, plan["account"])
        grant = "*" + win32security.ConvertSidToStringSid(sid) + ":(OI)(CI)RX"
        subprocess.run(
            ["icacls.exe", str(root), "/grant:r", grant, "/T"], check=True, capture_output=True
        )
        current = scm.inspect(plan["name"])
        owned(current, plan, image)
        handle = win32service.OpenService(
            scm.manager, plan["name"], win32service.SERVICE_QUERY_CONFIG
        )
        try:
            assert win32service.QueryServiceConfig(handle)[1] == win32service.SERVICE_AUTO_START
            recovery = win32service.QueryServiceConfig2(
                handle, win32service.SERVICE_CONFIG_FAILURE_ACTIONS
            )
            assert recovery["Actions"][-1] == (win32service.SC_ACTION_NONE, 0)
        finally:
            win32service.CloseServiceHandle(handle)
        scm.start(plan["name"])
        scm.wait(plan["name"], "running")
        first = scm.inspect(plan["name"])["pid"]
        scm.start(plan["name"])
        assert scm.inspect(plan["name"])["pid"] == first
        proof.append(dict(phase="idempotent-start", pid=first))
        process = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, first)
        try:
            win32api.TerminateProcess(process, 9)
        finally:
            process.Close()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            current = scm.inspect(plan["name"])
            if current["state"] == "running" and current["pid"] != first:
                break
            time.sleep(0.2)
        else:
            pytest.fail("Real SCM automatic recovery failed")
        proof.append(dict(phase="recovered", pid=current["pid"]))
        # Exhaust the second and final SCM restart, then prove the terminal no-action.
        for attempt in (2, 3):
            previous = scm.inspect(plan["name"])["pid"]
            process = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, previous)
            try:
                win32api.TerminateProcess(process, 9)
            finally:
                process.Close()
            if attempt == 2:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    current = scm.inspect(plan["name"])
                    if current["state"] == "running" and current["pid"] != previous:
                        break
                    time.sleep(0.2)
                else:
                    pytest.fail("Second bounded SCM recovery failed")
            else:
                scm.wait(plan["name"], "stopped")
                time.sleep(2)
                assert scm.inspect(plan["name"])["state"] == "stopped"
                proof.append(dict(phase="restart-budget-exhausted"))
        scm.stop(plan["name"])
        scm.wait(plan["name"], "stopped")
        scm.start(plan["name"])
        scm.wait(plan["name"], "running")
        proof.append(dict(phase="restarted", pid=scm.inspect(plan["name"])["pid"]))
        scm.stop(plan["name"])
        scm.wait(plan["name"], "stopped")
    except BaseException as exc:
        original_failure = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        diagnostics = {
            "service": plan["name"],
            "root": str(root),
            "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip(),
            "checks": proof,
            "failure": original_failure,
        }
        try:
            diagnostics["scm_query"] = subprocess.run(
                ["sc.exe", "queryex", plan["name"]], capture_output=True, text=True, timeout=15
            ).stdout
            diagnostics["scm_config"] = subprocess.run(
                ["sc.exe", "qc", plan["name"]], capture_output=True, text=True, timeout=15
            ).stdout
            diagnostics["private_import"] = subprocess.run(
                [
                    str(host / "python.exe"),
                    "-I",
                    "-B",
                    "-c",
                    "import native_fixture; print(native_fixture.Fixture._svc_name_)",
                ],
                capture_output=True,
                text=True,
                timeout=20,
            ).__dict__
            event_script = (
                "Get-WinEvent -FilterHashtable @{LogName='Application';StartTime=(Get-Date).AddMinutes(-10)} -MaxEvents 300 -ErrorAction SilentlyContinue | Where-Object { $_.ProviderName -match 'Python|Tester' -or $_.Message -like '*"
                + plan["name"]
                + "*' } | Select-Object TimeCreated,Id,ProviderName,Message | ConvertTo-Json -Depth 4"
            )
            diagnostics["application_events"] = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", event_script],
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout
        except Exception as exc:
            diagnostics["diagnostic_error"] = type(exc).__name__ + ": " + str(exc)
        try:
            current = scm.inspect(plan["name"])
            if current is not None:
                owned(current, plan, image)
                scm.stop(plan["name"])
                scm.wait(plan["name"], "stopped")
                scm.remove(plan["name"])
        except Exception as exc:
            diagnostics["cleanup_error"] = type(exc).__name__ + ": " + str(exc)
            if original_failure is None:
                raise
        finally:
            scm.close()
            (tmp_path / "native-scm-evidence.json").write_text(
                json.dumps(diagnostics, indent=2, default=str), encoding="utf-8"
            )
    check = NativeSCM()
    try:
        assert check.inspect(plan["name"]) is None
    finally:
        check.close()


def test_release_database_scope_refuses_system_and_external_cluster(tmp_path):
    import pytest

    marker = dict(root=str(tmp_path), ports={"api": 18767, "postgres": 55471}, source_sha="a" * 40)
    config = dict(
        host="127.0.0.1",
        port=18767,
        data_dir=str(tmp_path / "runtime"),
        frontend_dir=str(tmp_path / "releases" / ("a" * 40) / "frontend"),
        database_url="host=127.0.0.1 port=5432 dbname=tester user=tester_app",
    )
    with pytest.raises(ValueError):
        rs.validate_config(marker, config)
    config["database_url"] = "host=127.0.0.1 port=55471 dbname=tester user=tester_app"
    rs.validate_config(marker, config)
    config["data_dir"] = str(tmp_path.parent / "foreign")
    with pytest.raises(ValueError):
        rs.validate_config(marker, config)


def test_real_unicode_com_shortcut_roundtrip(tmp_path):
    import os

    import pytest

    from src.api.release_install import create_shortcuts

    if os.name != "nt":
        pytest.skip("WScript.Shell COM requires Windows")
    ident = uuid.uuid4()
    root = tmp_path / ("TesterReleaseTest_" + ident.hex)
    root.mkdir()
    desktop = tmp_path / ("Desktop_" + ident.hex)
    desktop.mkdir()
    marker = dict(
        mode="test",
        test_desktop=str(desktop),
        source_sha="a" * 40,
        trust_config=str(tmp_path / ("TesterReleaseMaintenance_" + ident.hex) / "trust.json"),
    )
    scripts = root / "releases" / marker["source_sha"] / "scripts/windows"
    scripts.mkdir(parents=True)
    for name in ("Start-ReleaseTester.ps1", "Uninstall-ReleaseTester.ps1"):
        (scripts / name).write_text("# signed release fixture")
    paths = create_shortcuts(root, marker)
    assert {__import__("pathlib").Path(path).name for path in paths} == {
        "TESTER — Запуск.lnk",
        "TESTER — Удаление.lnk",
    }
    assert create_shortcuts(root, marker) == paths
    import win32com.client

    shell = win32com.client.Dispatch("WScript.Shell")
    uninstall = shell.CreateShortcut(paths[1])
    assert '-TrustConfig "' + marker["trust_config"] + '"' in uninstall.Arguments
    assert " -Root " not in uninstall.Arguments
    assert uninstall.WorkingDirectory == str(root)


def test_startup_interruption_receipt_retains_exact_instance(tmp_path):
    import json

    from src.api.release_install import MARKER, SCHEMA

    ident = uuid.uuid4()
    root = tmp_path / ("TesterReleaseTest_" + ident.hex)
    root.mkdir()
    (root / "runtime").mkdir()
    marker = dict(
        schema=SCHEMA,
        root=str(root),
        installation_id=str(ident),
        source_sha="a" * 40,
        release_manifest_sha256="b" * 64,
        mode="test",
        test_desktop=str(tmp_path / ("Desktop_" + ident.hex)),
        services={
            "app": "TesterReleaseApp_" + ident.hex,
            "postgres": "TesterReleasePG_" + ident.hex,
        },
        ports={"api": 18767, "postgres": 55471},
    )
    (root / MARKER).write_text(json.dumps(marker))
    service = object.__new__(rs.ReleaseService)
    service.root = root
    service.instance = "exact-startup-instance"
    service.record_phase("starting")
    service.record_phase("startup-interrupted")
    receipt = json.loads((root / "runtime/service-lifecycle.json").read_text())
    assert receipt["installation_id"] == str(ident)
    assert receipt["source_sha"] == "a" * 40
    assert receipt["instance_id"] == "exact-startup-instance"
    assert [row["phase"] for row in receipt["transitions"]] == ["starting", "startup-interrupted"]


def test_release_postgres_config_refuses_include_and_system_port():
    import pytest

    from src.api.lab_scm import validate_pg_config

    validate_pg_config("listen_addresses = '127.0.0.1'\nport = 55471\n", expected_port=55471)
    for config in (
        "listen_addresses = '127.0.0.1'\nport = 5432\n",
        "listen_addresses = '127.0.0.1'\nport = 55471\ninclude = 'foreign.conf'\n",
    ):
        with pytest.raises(ValueError):
            validate_pg_config(config, expected_port=55471)


def test_shortcuts_require_explicit_elevated_launch_flag(tmp_path):
    import os

    import pytest

    from src.api.release_install import create_shortcuts

    if os.name != "nt":
        pytest.skip("Native Windows links require Windows")
    ident = uuid.uuid4()
    root = tmp_path / ("TesterReleaseTest_" + ident.hex)
    scripts = root / "releases" / ("a" * 40) / "scripts/windows"
    scripts.mkdir(parents=True)
    for name in ("Start-ReleaseTester.ps1", "Uninstall-ReleaseTester.ps1"):
        (scripts / name).write_text("# fixture")
    desktop = tmp_path / ("Desktop_" + ident.hex)
    desktop.mkdir()
    marker = dict(mode="test", test_desktop=str(desktop), source_sha="a" * 40)
    paths = create_shortcuts(root, marker)
    for path in paths:
        data = __import__("pathlib").Path(path).read_bytes()
        assert int.from_bytes(data[20:24], "little") & 0x2000


def test_new_native_registration_failure_rolls_back_exact_created_handle(monkeypatch):
    import sys
    import types

    import pytest

    from src.api.lab_scm import NativeSCM

    calls = []

    class WS:
        SERVICE_CHANGE_CONFIG = 2
        SERVICE_START = 16
        SERVICE_WIN32_OWN_PROCESS = 16
        SERVICE_AUTO_START = 2
        SERVICE_DEMAND_START = 3
        SERVICE_ERROR_NORMAL = 1
        SERVICE_CONFIG_FAILURE_ACTIONS = 2
        SC_ACTION_RESTART = 1
        SC_ACTION_NONE = 0

        def CreateService(self, *args):
            calls.append(("access", args[3]))
            return "new-owned-handle"

        def ChangeServiceConfig2(self, *args):
            raise RuntimeError("native recovery config failed")

        def DeleteService(self, h):
            calls.append(("delete", h))

        def CloseServiceHandle(self, h):
            calls.append(("close", h))

    scm = object.__new__(NativeSCM)
    scm.ws = WS()
    scm.manager = "manager"
    monkeypatch.setitem(sys.modules, "winreg", types.SimpleNamespace())
    plan = dict(name="TesterReleaseApp_" + uuid.uuid4().hex, recovery_delays_ms=[1000, 5000, 0])
    with pytest.raises(RuntimeError, match="native recovery config failed"):
        scm.install(plan, "private-host.exe", None)
    assert calls == [
        ("access", 2 | 16 | 0x10000),
        ("delete", "new-owned-handle"),
        ("close", "new-owned-handle"),
    ]


def test_service_sid_is_deterministic_and_case_insensitive():
    assert rs.service_sid("TesterReleaseApp_0123456789abcdef0123456789abcdef") == rs.service_sid(
        "TESTERRELEASEAPP_0123456789ABCDEF0123456789ABCDEF"
    )
    assert rs.service_sid("TesterReleaseApp_0123456789abcdef0123456789abcdef").startswith(
        "S-1-5-80-"
    )


def test_stop_proof_ignores_unrelated_protected_processes(monkeypatch):
    from contextlib import nullcontext

    marker = {"installation_id": "test"}
    calls = []

    class Controller:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def apply(self, action):
            calls.append(action)
            return [{"state": "stopped"}]

    class Proof:
        def prove_exit(self):
            calls.append("prove-exit")

    monkeypatch.setattr(rs, "NativeController", lambda marker: Controller())
    monkeypatch.setattr(
        rs, "capture_stop_proof", lambda marker: nullcontext(Proof()), raising=False
    )
    monkeypatch.setattr(
        rs,
        "writer_processes",
        lambda marker: (_ for _ in ()).throw(
            AssertionError("must not inspect unrelated protected processes")
        ),
        raising=False,
    )
    assert rs.stop_and_prove_exit(marker) == [{"state": "stopped"}]
    assert calls == ["stop", "prove-exit"]


def test_remove_revalidates_after_stop_wait_swap(monkeypatch, tmp_path):
    ident = uuid.uuid4()
    marker = dict(
        root=str(tmp_path),
        installation_id=str(ident),
        source_sha="a" * 40,
        services={
            "app": "TesterReleaseApp_" + ident.hex,
            "postgres": "TesterReleasePG_" + ident.hex,
        },
        ports={"api": 18767, "postgres": 55471},
    )
    app, pg = rs.plans(marker)
    import subprocess

    states = {
        p["name"]: dict(
            image=p.get("image", subprocess.list2cmdline([p.get("host", "")])),
            account=p["account"],
            root=str(tmp_path),
            source_sha="a" * 40,
            installation_id=str(ident),
            state="running",
            executable=p["executable"],
        )
        for p in (app, pg)
    }
    removed = []

    class Swapped:
        def inspect(self, name):
            return states[name]

        def stop(self, name):
            states[name]["state"] = "stopped"

        def wait(self, name, state):
            if name == app["name"]:
                states[name]["image"] = "FOREIGN.exe"

        def remove(self, name):
            removed.append(name)

    controller = object.__new__(rs.NativeController)
    controller.marker = marker
    controller.scm = Swapped()
    import pytest

    with pytest.raises(ValueError):
        controller.apply("remove")
    assert not removed


def test_native_lifecycle_retains_original_service_object(monkeypatch):
    from src.api.lab_scm import ACCOUNT, NativeSCM

    calls = []
    plan = dict(
        name="TesterReleaseApp_" + uuid.uuid4().hex,
        installation_id=str(uuid.uuid4()),
        root="owned",
        account=ACCOUNT,
    )
    states = {
        "original": dict(
            image="owned.exe",
            installation_id=plan["installation_id"],
            root="owned",
            account=ACCOUNT,
        )
    }

    class API:
        SERVICE_QUERY_CONFIG = 1
        SERVICE_QUERY_STATUS = 4
        SERVICE_START = 16
        SERVICE_STOP = 32

        def OpenService(self, manager, name, access):
            calls.append(("open", name))
            return "original"

        def CloseServiceHandle(self, h):
            calls.append(("close", h))

        def DeleteService(self, h):
            calls.append(("delete", h))

    scm = object.__new__(NativeSCM)
    scm.ws = API()
    scm.manager = "manager"
    scm._bound = None
    scm._bound_plans = {}
    monkeypatch.setattr(scm, "_inspect_handle", lambda name, h: states[h])
    with scm.lease([(plan, "owned.exe")]):
        # Even a simulated name replacement cannot redirect the retained handle.
        scm.ws.OpenService = lambda *args: (_ for _ in ()).throw(
            AssertionError("reopened mutable name")
        )
        scm.remove(plan["name"])
    assert calls == [("open", plan["name"]), ("delete", "original"), ("close", "original")]


def test_native_host_budget_denial_is_normal_stop_decision(monkeypatch, tmp_path):
    import sys
    import types

    calls = []

    class Denied(ValueError):
        pass

    class Budget:
        @classmethod
        def from_config(cls, *args):
            calls.append(args)
            return cls()

        def admit(self, role):
            calls.append(role)
            raise Denied("durable budget exhausted")

    monkeypatch.setitem(
        sys.modules,
        "src.api.restart_budget",
        types.SimpleNamespace(DatabaseRestartBudget=Budget, BudgetDenied=Denied),
    )
    marker = dict(installation_id=str(uuid.uuid4()), source_sha="a" * 40)
    monkeypatch.setattr(rs, "load_marker", lambda root: marker)
    service = object.__new__(rs.ReleaseService)
    assert service.admit_start(tmp_path) is False
    assert calls[-1] == "host"


def test_native_process_image_exit_is_a_transition(tmp_path):
    import os
    import subprocess
    import sys
    import pytest

    if os.name != "nt":
        pytest.skip("Native process image requires Windows")
    from src.api.lab_scm import NativeSCM

    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=15)
    with pytest.raises(ProcessLookupError):
        NativeSCM.process_image(child.pid)
