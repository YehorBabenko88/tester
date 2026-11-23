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
    def __init__(self,args):
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
    finally:
        current = scm.inspect(plan["name"])
        if current is not None:
            owned(current, plan, image)
            scm.stop(plan["name"])
            scm.wait(plan["name"], "stopped")
            scm.remove(plan["name"])
        scm.close()
    check = NativeSCM()
    try:
        assert check.inspect(plan["name"]) is None
    finally:
        check.close()
    (tmp_path / "native-scm-evidence.json").write_text(
        json.dumps(dict(service=plan["name"], root=str(root), checks=proof), indent=2)
    )


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
    marker = dict(mode="test", test_desktop=str(desktop), source_sha="a" * 40)
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
