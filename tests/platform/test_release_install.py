import sys
import uuid

import pytest

from src.api import release_install as ri


def _open_observed_process(pid):
    import pywintypes
    import win32api

    try:
        return win32api.OpenProcess(0x1000, False, pid)
    except pywintypes.error as exc:
        if exc.winerror == 87:  # Snapshot PID exited before OpenProcess.
            return None
        raise


@pytest.mark.skipif(sys.platform != "win32", reason="native pywin32 OpenProcess exception contract")
def test_observed_process_only_ignores_exited_pid(monkeypatch):
    import pywintypes
    import win32api

    def fail(code):
        def opening(*args):
            raise pywintypes.error(code, "OpenProcess", "native observation race")

        return opening

    monkeypatch.setattr(win32api, "OpenProcess", fail(87))
    assert _open_observed_process(1234) is None
    monkeypatch.setattr(win32api, "OpenProcess", fail(5))
    with pytest.raises(pywintypes.error):
        _open_observed_process(1234)


def test_test_root_requires_unique_name_and_fake_desktop(tmp_path):
    with pytest.raises(ValueError):
        ri.validate_destination(tmp_path / "ordinary", mode="test", desktop=tmp_path / "desktop")
    root = tmp_path / ("TesterReleaseTest_" + uuid.uuid4().hex)
    ri.validate_destination(root, mode="test", desktop=root.parent / ("Desktop_" + root.name))
    with pytest.raises(ValueError):
        ri.validate_destination(tmp_path / "TesterLab", mode="production", desktop=None)


def test_lifecycle_lock_excludes_second_owner(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with ri.lifecycle_lock(root):
        with pytest.raises(ValueError):
            with ri.lifecycle_lock(root):
                pass
    with ri.lifecycle_lock(root):
        pass


def test_marker_authority_remains_fail_closed_under_optimized_python(tmp_path):
    import json
    import subprocess
    import sys

    ident = uuid.uuid4()
    root = tmp_path / ("TesterReleaseTest_" + ident.hex)
    root.mkdir()
    marker = dict(
        schema=ri.SCHEMA,
        root=str(root),
        installation_id=str(ident),
        source_sha="a" * 40,
        release_manifest_sha256="b" * 64,
        mode="test",
        test_desktop=str(tmp_path / ("Desktop_" + ident.hex)),
        services={"app": "ForeignService", "postgres": "PostgreSQL"},
        ports={"api": 5432, "postgres": 5432},
    )
    (root / ri.MARKER).write_text(json.dumps(marker))
    result = subprocess.run(
        [
            sys.executable,
            "-O",
            "-c",
            "from src.api.release_install import load_marker; import sys; load_marker(sys.argv[1])",
            str(root),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "invalid release ownership marker" in result.stderr


def test_full_native_installer_lifecycle(tmp_path):
    """Elevated disposable Windows only: real PG, API/worker and safe full removal."""
    import ctypes
    import hashlib
    import json
    import os
    import shutil
    import subprocess
    import time
    from pathlib import Path
    from urllib.error import HTTPError
    from urllib.request import urlopen

    required = os.environ.get("TESTER_REQUIRE_FULL_INSTALLER") == "1"
    if not required:
        pytest.skip("Full native installer requires explicit disposable-runner opt-in")
    if os.name != "nt" or not ctypes.windll.shell32.IsUserAnAdmin():
        pytest.fail("Full installer opt-in requires elevated disposable Windows runner")
    required_names = (
        "TESTER_RELEASE_BUNDLE",
        "TESTER_RELEASE_MANIFEST_SHA256",
        "TESTER_RELEASE_PUBLIC_KEY_SHA256",
        "TESTER_RELEASE_SOURCE_SHA",
    )
    if any(not os.environ.get(name) for name in required_names):
        pytest.fail("Full installer opt-in requires signed fixture bundle and independent pins")
    import psycopg
    import win32api
    import win32con
    import win32security

    from src.api.lab_scm import NativeSCM
    from src.api.release_security import verify_release
    from src.api.release_service import NativeController, service_sid, validate_resources

    ident = uuid.uuid4().hex
    root = tmp_path / ("TesterReleaseTest_" + ident)
    desktop = tmp_path / ("Desktop_" + ident)
    desktop.mkdir()
    media = tmp_path / ("InstallerMedia_" + ident)
    shutil.copytree(Path(os.environ["TESTER_RELEASE_BUNDLE"]), media)
    source_sha = os.environ["TESTER_RELEASE_SOURCE_SHA"]
    key_pin = os.environ["TESTER_RELEASE_PUBLIC_KEY_SHA256"]
    manifest_pin = os.environ["TESTER_RELEASE_MANIFEST_SHA256"]
    if hashlib.sha256((media / "release-manifest.json").read_bytes()).hexdigest() != manifest_pin:
        pytest.fail("Fixture manifest pin mismatch")
    verify_release(media, trusted_public_key_sha256=key_pin, expected_source_sha=source_sha)
    evidence = dict(
        source_sha=source_sha,
        signature_scope="FIXTURE_ONLY_NOT_PRODUCTION_TRUST",
        root=str(root),
        checks=[],
    )
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GUARDIANS_ENABLED="false")
    marker = None
    failure = None

    def powershell(args, *, input_text=None, timeout=120, cwd=None):
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", *map(str, args)],
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
            cwd=cwd,
        )
        if result.returncode:
            raise AssertionError(
                "Native PowerShell failed: " + result.stdout[-6000:] + "\n" + result.stderr[-6000:]
            )
        return result

    def system_pg():
        script = "@{services=@(Get-CimInstance Win32_Service | Where-Object {$_.Name -match 'postgres' -or $_.PathName -match 'PostgreSQL'} | Select-Object Name,PathName,StartName,StartMode,State,ProcessId);listeners=@(Get-NetTCPConnection -LocalPort 5432 -State Listen -ErrorAction SilentlyContinue | Select-Object LocalAddress,LocalPort,OwningProcess)} | ConvertTo-Json -Depth 6 -Compress"
        return json.loads(powershell(["-Command", script]).stdout)

    def health():
        try:
            response = urlopen(
                "http://127.0.0.1:" + str(marker["ports"]["api"]) + "/api/health", timeout=3
            )
        except HTTPError as exc:
            if exc.code != 503:
                raise
            response = exc
        with response:
            return json.loads(response.read())["data"]

    def ready(*, different_instance=None):
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            try:
                data = health()
                native = json.loads((root / "runtime/service.json").read_text())
                if (
                    all(data.get(key) == "ok" for key in ("database", "worker", "storage"))
                    and data.get("instance_id") == native["instance_id"]
                    and native["source_sha"] == source_sha
                    and native["installation_id"] == marker["installation_id"]
                    and (different_instance is None or native["instance_id"] != different_instance)
                ):
                    return native
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(0.3)
        pytest.fail("Real API/worker/storage readiness deadline exceeded")

    def kill_owned(pid, *, role, allowed):
        handle = win32api.OpenProcess(win32con.PROCESS_TERMINATE | 0x1000, False, pid)
        token = None
        try:
            token = win32security.OpenProcessToken(handle, win32con.TOKEN_QUERY)
            sid = win32security.ConvertSidToStringSid(
                win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            )
            if sid != service_sid(marker["services"][role]):
                pytest.fail("Refused fault injection into foreign process token")
            from src.api.lab_scm import NativeSCM

            if NativeSCM.process_image(pid).casefold() not in {str(p).casefold() for p in allowed}:
                pytest.fail("Refused fault injection into foreign executable")
            win32api.TerminateProcess(handle, 9)
        finally:
            if token is not None:
                token.Close()
            handle.Close()

    def component_leaves(component):
        script = (
            "Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -like '*-m src.api."
            + component
            + "*'} | Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"
        )
        rows = json.loads(powershell(["-Command", script]).stdout or "[]")
        rows = rows if isinstance(rows, list) else [rows]
        own = []
        for row in rows:
            handle = _open_observed_process(row["ProcessId"])
            if handle is None:
                continue
            token = None
            try:
                token = win32security.OpenProcessToken(handle, win32con.TOKEN_QUERY)
                sid = win32security.ConvertSidToStringSid(
                    win32security.GetTokenInformation(token, win32security.TokenUser)[0]
                )
                if sid == service_sid(marker["services"]["app"]):
                    own.append(row)
            finally:
                if token is not None:
                    token.Close()
                handle.Close()
        parents = {r["ParentProcessId"] for r in own}
        leaves = [r for r in own if r["ProcessId"] not in parents]
        return leaves

    def start_types(names):
        import win32service

        from src.api.lab_scm import NativeSCM

        scm = NativeSCM()
        try:
            result = {}
            for name in names:
                handle = win32service.OpenService(
                    scm.manager, name, win32service.SERVICE_QUERY_CONFIG
                )
                try:
                    result[name] = {
                        win32service.SERVICE_AUTO_START: "auto",
                        win32service.SERVICE_DEMAND_START: "demand",
                    }.get(win32service.QueryServiceConfig(handle)[1], "other")
                finally:
                    win32service.CloseServiceHandle(handle)
            return result
        finally:
            scm.close()

    def preshutdown(names):
        import win32service

        scm = NativeSCM()
        try:
            result = []
            for name in names:
                handle = win32service.OpenService(
                    scm.manager, name, win32service.SERVICE_QUERY_CONFIG
                )
                try:
                    result.append(win32service.QueryServiceConfig2(handle, 7))
                finally:
                    win32service.CloseServiceHandle(handle)
            return result
        finally:
            scm.close()

    def assert_no_leftovers(markers):
        import winreg

        assert not root.exists()
        assert not list(desktop.glob("*.lnk"))
        assert not list(tmp_path.glob("TesterReleaseMaintenance_*")), "maintenance metadata left"
        assert not ri.lifecycle_lock_path(root).exists(), "lifecycle lock file left"
        names = [name for item in markers for name in item["services"].values()]
        deadline = time.monotonic() + 30
        while True:
            present = []
            for name in names:
                try:
                    winreg.OpenKey(
                        winreg.HKEY_LOCAL_MACHINE, "SYSTEM\\CurrentControlSet\\Services\\" + name
                    ).Close()
                    present.append(name)
                except FileNotFoundError:
                    pass
            if not present:
                break
            if time.monotonic() > deadline:
                pytest.fail("Service registry keys remain: " + ", ".join(present))
            time.sleep(0.5)

    def interrupted_setup(setup_media, phase, log_name, checkpoint=lambda: True):
        """Kill only the authenticated private installer Python at a test checkpoint."""
        nonlocal marker
        paused_env = dict(env, TESTER_INSTALL_TEST_PAUSE_PHASE=phase)
        bootstrap_log = tmp_path / log_name
        with bootstrap_log.open("w", encoding="utf-8") as output:
            process = subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-File",
                    str(setup_media / "Setup-Tester.ps1"),
                    "-Bundle",
                    str(setup_media),
                    "-Root",
                    str(root),
                    "-Mode",
                    "test",
                    "-Desktop",
                    str(desktop),
                    "-ManifestSHA256",
                    manifest_pin,
                    "-TrustedPublicKeySHA256",
                    key_pin,
                    "-ExpectedSourceSHA",
                    source_sha,
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                env=paused_env,
            )
            deadline = time.monotonic() + 600
            pause = None
            old_marker = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    pytest.fail(
                        "Interrupted-install bootstrap exited before its test checkpoint: "
                        + bootstrap_log.read_text()[-5000:]
                    )
                try:
                    old_marker = ri.load_marker(root)
                    marker = old_marker
                    pause_path = Path(old_marker["trust_config"]).parent / "test-pause.json"
                    pause = json.loads(pause_path.read_text())
                    if pause.get("phase") == phase and checkpoint():
                        break
                except (OSError, ValueError, KeyError):
                    pass
                time.sleep(0.2)
            else:
                pytest.fail("Real provisioning service interruption checkpoint was not reached")
            if (
                pause.get("root") != str(root)
                or pause.get("source_sha") != source_sha
                or pause.get("installation_id") != old_marker["installation_id"]
            ):
                pytest.fail("Fault checkpoint identity mismatch")
            pid = int(pause["pid"])
            actual_parent = json.loads(
                powershell(
                    [
                        "-Command",
                        "Get-CimInstance Win32_Process -Filter 'ProcessId="
                        + str(pid)
                        + "' | Select-Object ParentProcessId | ConvertTo-Json -Compress",
                    ]
                ).stdout
            )
            if actual_parent["ParentProcessId"] != process.pid:
                pytest.fail("Fault target is outside the created installer process")
            if (
                NativeSCM.process_image(pid).casefold()
                != str(setup_media / "runtimes/python/python.exe").casefold()
            ):
                pytest.fail("Fault target is not the verified private installer interpreter")
            handle = win32api.OpenProcess(
                win32con.PROCESS_TERMINATE | 0x100000 | 0x1000, False, pid
            )
            try:
                win32api.TerminateProcess(handle, 9)
            finally:
                handle.Close()
            assert process.wait(timeout=30) != 0
        return old_marker

    def setup(setup_media, *, timeout=1200):
        return powershell(
            [
                "-File",
                setup_media / "Setup-Tester.ps1",
                "-Bundle",
                setup_media,
                "-Root",
                root,
                "-Mode",
                "test",
                "-Desktop",
                desktop,
                "-ManifestSHA256",
                manifest_pin,
                "-TrustedPublicKeySHA256",
                key_pin,
                "-ExpectedSourceSHA",
                source_sha,
            ],
            timeout=timeout,
        )

    before = system_pg()
    try:
        # Round 2: an existing EMPTY root is never adopted. One that grants Users
        # write access is refused untouched before any transaction exists ...
        import pywintypes
        import win32file
        import win32security

        from src.api.release_uninstall import _identity as removal_identity

        def admin_root(extra_aces=()):
            descriptor = win32security.SECURITY_DESCRIPTOR()
            descriptor.SetSecurityDescriptorOwner(
                win32security.ConvertStringSidToSid("S-1-5-32-544"), False
            )
            acl = win32security.ACL()
            for sid, mask in (("S-1-5-18", 0x1F01FF), ("S-1-5-32-544", 0x1F01FF), *extra_aces):
                acl.AddAccessAllowedAceEx(
                    win32security.ACL_REVISION_DS, 3, mask, win32security.ConvertStringSidToSid(sid)
                )
            descriptor.SetSecurityDescriptorDacl(True, acl, False)
            descriptor.SetSecurityDescriptorControl(
                win32security.SE_DACL_PROTECTED, win32security.SE_DACL_PROTECTED
            )
            attributes = pywintypes.SECURITY_ATTRIBUTES()
            attributes.SECURITY_DESCRIPTOR = descriptor
            win32file.CreateDirectory(str(root), attributes)

        admin_root(extra_aces=[("S-1-5-32-545", 0x1301BF)])
        with pytest.raises(AssertionError, match="Remove the empty folder"):
            setup(media, timeout=600)
        assert root.is_dir() and not any(root.iterdir())
        assert not list(tmp_path.glob("TesterReleaseMaintenance_*"))
        evidence["checks"].append(dict(phase="user-writable-empty-root-refused"))
        root.rmdir()
        # ... while an Administrators-owned clean empty root is deleted and created fresh.
        admin_root()
        precreated_identity = removal_identity(root)
        # First run is interrupted by killing only its authenticated private
        # bootstrap Python while the real same-account provisioning service runs.
        old_marker = interrupted_setup(
            media,
            "provisioning-actor-running",
            "interrupted-bootstrap.log",
            checkpoint=lambda: (root / "dependencies/venv").is_dir(),
        )
        from src.api.release_uninstall import _security_record, _WindowsObject

        obj = _WindowsObject(root / "dependencies/venv", lock=False)
        try:
            assert _security_record(obj)["owner"] == service_sid(old_marker["services"]["app"])
        finally:
            obj.close()
        assert removal_identity(root) != precreated_identity, "pre-created root object reused"
        evidence["checks"].append(dict(phase="empty-precreated-root-replaced"))
        evidence["checks"].append(
            dict(
                phase="real-install-driver-interrupted",
                installation_id=old_marker["installation_id"],
            )
        )
        setup(media)
        marker = ri.load_marker(root)
        native = ready()
        assert marker["installation_id"] != old_marker["installation_id"]
        assert set(start_types(marker["services"].values()).values()) == {"auto"}
        assert preshutdown([marker["services"]["app"], marker["services"]["postgres"]]) == [
            ri.PRESHUTDOWN_TIMEOUT_MS["app"],
            ri.PRESHUTDOWN_TIMEOUT_MS["postgres"],
        ]
        assert not Path(old_marker["trust_config"]).parent.exists(), "rolled-back metadata left"
        old_scm = NativeSCM()
        try:
            assert all(old_scm.inspect(name) is None for name in old_marker["services"].values())
        finally:
            old_scm.close()
        evidence["checks"].append(
            dict(phase="authenticated-interruption-rollback-and-clean-reinstall")
        )
        assert len(marker["services"]) == 2 and marker["ports"]["postgres"] != 5432
        evidence["checks"].append(
            dict(
                phase="installed-ready",
                instance_id=native["instance_id"],
                services=validate_resources(marker),
            )
        )
        config = json.loads((root / "config.json").read_text())
        assert config.get("provider_factory") == "src.integration.reference_prices:create_reader"
        assert config.get("handler_factory") == "src.integration.reference_prices:create_handler"
        assert config.get("market_catalog_enabled") is True
        owner = json.loads((root / "private/database-owner.json").read_text())
        with psycopg.connect(owner["database_url"]) as conn:
            conn.execute("CREATE SCHEMA tester_acceptance")
            conn.execute("CREATE TABLE tester_acceptance.lifecycle (value text PRIMARY KEY)")
            conn.execute(
                "INSERT INTO tester_acceptance.lifecycle VALUES (%s)", ("durable-" + ident,)
            )
            assert conn.execute("SELECT count(*) FROM tester_platform.workers").fetchone()[0] >= 1
            assert (
                conn.execute("SELECT count(*) FROM tester_storage.schema_migrations").fetchone()[0]
                > 0
            )
        links = sorted(desktop.glob("*.lnk"))
        assert {p.name for p in links} == {"TESTER — Запуск.lnk", "TESTER — Удаление.lnk"}
        start_path = desktop / "TESTER — Запуск.lnk"
        start_link = ri.read_shortcut(start_path)
        exact_command = (
            subprocess.list2cmdline([start_link["target"]])
            + " "
            + start_link["arguments"]
            + " -NoBrowser"
        )
        for _ in range(2):
            result = subprocess.run(
                exact_command, capture_output=True, text=True, timeout=120, env=env
            )
            if result.returncode:
                raise AssertionError(
                    "Installed launch shortcut command failed: " + result.stderr[-5000:]
                )
        assert ready()["supervisor_pid"] == native["supervisor_pid"]
        evidence["checks"].append(
            dict(
                phase="exact-shortcut-command-idempotent",
                browser_scope="NoBrowser; browser acceptance separate",
            )
        )
        if os.environ.get("TESTER_REQUIRE_INSTALLED_BROWSER") != "1":
            pytest.fail("Full installed lifecycle requires explicit real-browser acceptance")
        frontend = Path(__file__).resolve().parents[2] / "frontend"
        browser_env = dict(
            env,
            TESTER_INSTALLED_URL="http://127.0.0.1:" + str(marker["ports"]["api"]),
            TESTER_INSTALLED_INSTANCE=native["instance_id"],
            TESTER_INSTALLED_SOURCE_SHA=source_sha,
            TESTER_INSTALLED_APP_SHA256=hashlib.sha256(
                (root / "releases" / source_sha / "frontend/app.js").read_bytes()
            ).hexdigest(),
        )
        browser = subprocess.run(
            [
                "node",
                str(frontend / "node_modules/@playwright/test/cli.js"),
                "test",
                "--config",
                str(frontend / "playwright.release-installed.config.js"),
            ],
            cwd=frontend,
            env=browser_env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if browser.returncode:
            pytest.fail(
                "Installed SCM browser gate failed: "
                + browser.stdout[-6000:]
                + browser.stderr[-3000:]
            )
        assert ready()["supervisor_pid"] == native["supervisor_pid"]
        evidence["checks"].append(
            dict(
                phase="real-installed-scm-browser-terminal-ready",
                source_sha=source_sha,
                instance_id=native["instance_id"],
                duplicate_launch_same_instance=True,
            )
        )
        with NativeController(marker) as controller:
            controller.apply("restart")
        native = ready(different_instance=native["instance_id"])
        # Kill a real API and worker child, authenticated by unique service SID.
        for component in ("worker", "main"):
            leaves = component_leaves(component)
            assert len(leaves) == 1
            old_pid = leaves[0]["ProcessId"]
            kill_owned(
                old_pid,
                role="app",
                allowed=(
                    root / "dependencies/python/python.exe",
                    root / "dependencies/venv/Scripts/python.exe",
                ),
            )
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                recovered = component_leaves(component)
                if len(recovered) == 1 and recovered[0]["ProcessId"] != old_pid:
                    break
                time.sleep(0.5)
            else:
                pytest.fail("Real child process did not recover with a new PID")
            assert ready()["supervisor_pid"] == native["supervisor_pid"]
            evidence["checks"].append(
                dict(
                    phase="child-recovered",
                    component=component,
                    old_pid=old_pid,
                    new_pid=recovered[0]["ProcessId"],
                )
            )
        app_state = validate_resources(marker)[0]
        previous_instance = native["instance_id"]
        kill_owned(
            app_state["pid"], role="app", allowed=(root / "dependencies/service/pythonservice.exe",)
        )
        native = ready(different_instance=previous_instance)
        evidence["checks"].append(
            dict(phase="scm-host-recovered", instance_id=native["instance_id"])
        )
        with psycopg.connect(owner["database_url"]) as conn:
            assert (
                conn.execute("SELECT value FROM tester_acceptance.lifecycle").fetchone()[0]
                == "durable-" + ident
            )
        evidence["checks"].append(dict(phase="database-durability-after-recovery"))
        # #108B: the user deletes the uninstall shortcut and re-runs Setup.
        uninstall_link = desktop / "TESTER — Удаление.lnk"
        uninstall_link.unlink()
        trust_path = Path(marker["trust_config"])
        ledger_before = json.loads(trust_path.read_text(encoding="utf-8"))["ledger"]
        # A successful repeated setup must not create additional services/processes.
        setup(media, timeout=180)
        assert ready()["supervisor_pid"] == native["supervisor_pid"]
        assert uninstall_link.is_file()
        ledger_after = json.loads(trust_path.read_text(encoding="utf-8"))["ledger"]
        assert ledger_after != ledger_before and not Path(ledger_before).exists()
        evidence["checks"].append(dict(phase="recreated-shortcut-reenrolled"))
        # #108A: documented administrator kill switch, created as documented.
        kill_switch = root / "runtime" / "restart.disabled"
        powershell(
            [
                "-Command",
                "New-Item -ItemType File -Path '"
                + str(kill_switch)
                + "' | Out-Null; icacls '"
                + str(kill_switch)
                + "' /setowner '*S-1-5-32-544' | Out-Null",
            ]
        )
        assert kill_switch.is_file()
        # Original installation media is unavailable during transient uninstallation.
        renamed = tmp_path / ("UnavailableOriginalMedia_" + ident)
        assert media.parent == renamed.parent == tmp_path
        os.replace(media, renamed)
        uninstall = root / "releases" / source_sha / "scripts/windows/Uninstall-ReleaseTester.ps1"
        # R1: the uninstaller may be started with its current directory inside the root.
        result = powershell(
            ["-File", uninstall, "-TrustConfig", marker["trust_config"]],
            input_text="DELETE TESTER\nYES\n",
            timeout=600,
            cwd=root,
        )
        evidence["checks"].append(
            dict(phase="transient-uninstalled-without-original-media", output=result.stdout[-3000:])
        )
        assert all(state is None for state in validate_resources(marker))
        assert_no_leftovers([old_marker, marker])
        evidence["checks"].append(dict(phase="absence-verified-no-leftovers"))
        first_installation = marker["installation_id"]
        media2 = tmp_path / ("SecondCleanInstallerMedia_" + ident)
        shutil.copytree(Path(os.environ["TESTER_RELEASE_BUNDLE"]), media2)
        # #107: interrupt after services are registered but before the commit.
        first_marker = marker
        sealed = interrupted_setup(media2, "sealing-ownership", "interrupted-sealing.log")
        assert set(start_types(sealed["services"].values()).values()) == {"demand"}
        # Even if an operator (or a stale boot) starts PostgreSQL, it creates
        # PostgreSQL-SID objects that rollback must still authenticate and remove.
        from src.api.lab_scm import NativeSCM

        pg_scm = NativeSCM(mutating=True)
        try:
            pg_scm.start(sealed["services"]["postgres"])
            pg_scm.wait(sealed["services"]["postgres"], "running")
        finally:
            pg_scm.close()
        evidence["checks"].append(dict(phase="uncommitted-services-demand-start"))
        setup(media2)
        marker = ri.load_marker(root)
        assert marker["installation_id"] not in (
            first_installation,
            sealed["installation_id"],
        )
        assert all(state is None for state in validate_resources(sealed))
        assert not Path(sealed["trust_config"]).parent.exists()
        evidence["checks"].append(dict(phase="uncommitted-postgres-objects-rolled-back"))
        second_native = ready()
        evidence["checks"].append(
            dict(
                phase="second-clean-install-same-path-ready",
                installation_id=marker["installation_id"],
            )
        )
        owner2 = json.loads((root / "private/database-owner.json").read_text())
        with psycopg.connect(owner2["database_url"]) as conn:
            conn.execute("CREATE SCHEMA tester_acceptance")
            conn.execute("CREATE TABLE tester_acceptance.lifecycle(value text PRIMARY KEY)")
            conn.execute(
                "INSERT INTO tester_acceptance.lifecycle VALUES (%s)",
                ("pg-fault-durable-" + ident,),
            )
        pg_before = validate_resources(marker)[1]
        evidence["checks"].append(
            dict(phase="before-real-postgres-service-fault", pid=pg_before["pid"])
        )
        kill_owned(
            pg_before["pid"],
            role="postgres",
            allowed=(root / "dependencies/service/pythonservice.exe",),
        )
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            current = validate_resources(marker)[1]
            if current and current["state"] == "running" and current["pid"] != pg_before["pid"]:
                break
            time.sleep(0.3)
        else:
            pytest.fail("Real private PostgreSQL SCM service did not automatically recover")
        recovered = ready()
        with psycopg.connect(owner2["database_url"]) as conn:
            assert (
                conn.execute("SELECT value FROM tester_acceptance.lifecycle").fetchone()[0]
                == "pg-fault-durable-" + ident
            )
        evidence["checks"].append(
            dict(
                phase="real-postgres-service-recovered-data-retained",
                old_pid=pg_before["pid"],
                new_pid=current["pid"],
                app_instance=recovered["instance_id"],
                initial_app_instance=second_native["instance_id"],
            )
        )
        powershell(
            [
                "-File",
                root / "releases" / source_sha / "scripts/windows/Uninstall-ReleaseTester.ps1",
                "-TrustConfig",
                marker["trust_config"],
            ],
            input_text="DELETE TESTER\nYES\n",
            timeout=600,
        )
        assert all(state is None for state in validate_resources(marker))
        assert_no_leftovers([old_marker, first_marker, sealed, marker])
        evidence["checks"].append(dict(phase="second-clean-install-cleanup-complete"))

    except BaseException as exc:
        failure = type(exc).__name__ + ": " + str(exc)
        evidence["failure"] = failure
        raise
    finally:
        try:
            after = system_pg()
            evidence["system_postgres_unchanged"] = before == after
            if before != after and failure is None:
                pytest.fail("Unrelated PostgreSQL service/listener snapshot changed")
            if marker is not None and root.exists():
                try:
                    from src.api.lab_scm import NativeSCM

                    diagnostic_scm = NativeSCM()
                    try:
                        status = [
                            diagnostic_scm.inspect(name) for name in marker["services"].values()
                        ]
                        evidence["final_service_status"] = [
                            {
                                key: row.get(key)
                                for key in ("name", "state", "pid", "executable", "python_class")
                            }
                            if row
                            else None
                            for row in status
                        ]
                    finally:
                        diagnostic_scm.close()
                    pidfile = root / "postgres-data/postmaster.pid"
                    if pidfile.exists():
                        postmaster = int(pidfile.read_text().splitlines()[0])
                        evidence["final_postmaster_pid"] = postmaster
                        try:
                            evidence["final_postmaster_image"] = NativeSCM.process_image(postmaster)
                        except Exception as diagnostic_error:
                            evidence["final_postmaster_query_error"] = type(
                                diagnostic_error
                            ).__name__
                except Exception as diagnostic_error:
                    evidence["final_status_query_error"] = type(diagnostic_error).__name__
                try:
                    from src.api import release_provision

                    release_provision.stop_and_prove_exit(marker)
                    release_provision.remove_resources(marker)
                    evidence["failure_cleanup"] = (
                        "owned services removed; installation files preserved for diagnosis"
                    )
                except Exception as exc:
                    evidence["failure_cleanup_error"] = type(exc).__name__ + ": " + str(exc)
        finally:
            (tmp_path / "full-native-installer-evidence.json").write_text(
                json.dumps(evidence, indent=2, default=str), encoding="utf-8"
            )


def test_provisioning_plan_reuses_app_account_without_automatic_retries(tmp_path):
    from src.api.release_provision import provisioning_plan
    from src.api.release_service import plans

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
    actor = provisioning_plan(marker)
    app, _ = plans(marker)
    assert actor["name"] == app["name"] and actor["account"] == app["account"]
    assert actor["host"] == app["host"]
    assert actor["auto_start"] is False
    assert actor["recovery_delays_ms"] == []
    assert actor["dependency"] is None
    assert actor["python_class"].endswith(".ReleaseProvisionService")


def test_native_provisioning_root_guard_opens_and_pins_without_scm(tmp_path):
    import os

    import pytest

    if os.name != "nt":
        pytest.skip("Native Windows directory guard requires Windows")
    from src.api.release_provision import open_directory_guard

    root = tmp_path / "own-empty-root"
    root.mkdir()
    moved = tmp_path / "moved-root"
    root.rename(moved)
    moved.rename(root)
    guard = open_directory_guard(root)
    try:
        with pytest.raises(PermissionError):
            root.rename(moved)
    finally:
        guard.Close()
