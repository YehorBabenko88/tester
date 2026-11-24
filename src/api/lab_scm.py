"""Explicit, scoped SCM controller. Imports have no native side effects."""

import json
import os
import re
import subprocess
import time
from pathlib import Path

from .lab_install import LabViolation, check, plain, verify_tree

ACCOUNT = "NT AUTHORITY\\LocalService"


def validate_pg_config(text, *, expected_port=55470):
    allowed = {
        "listen_addresses",
        "port",
        "max_connections",
        "shared_buffers",
        "work_mem",
        "max_parallel_workers",
        "max_wal_size",
        "min_wal_size",
        "timezone",
        "password_encryption",
        "logging_collector",
        "log_statement",
        "log_min_error_statement",
    }
    values = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"([a-z_]+)\s*=\s*(.+)", line)
        if not match or match[1] not in allowed or match[1] in values:
            raise LabViolation("Unsupported or duplicate PostgreSQL configuration")
        values[match[1]] = match[2].strip()
    if values.get("port") != str(expected_port) or values.get("listen_addresses") != "'127.0.0.1'":
        raise LabViolation("Dedicated PostgreSQL port/listen configuration required")


def _pg_control(root):
    env = dict(os.environ, LANG="C", LC_ALL="C", LC_MESSAGES="C")
    result = subprocess.run(
        [
            r"C:\Program Files\PostgreSQL\18\bin\pg_controldata.exe",
            "-D",
            str(plain(root / "postgres-data")),
        ],
        env=env,
        capture_output=True,
        timeout=15,
        check=True,
    )
    return result.stdout.decode("utf-8", errors="strict")


def validate_pg_identity(root, probe=_pg_control):
    root = plain(root)
    m = check(root)
    recorded = json.loads(plain(root / "state/postgres-identity.json").read_text())
    identifiers = re.findall(
        r"^Database system identifier:\s*([0-9]+)\s*$", probe(root), re.MULTILINE
    )
    if len(identifiers) != 1 or recorded != dict(
        installation_id=m["installation_id"], system_identifier=identifiers[0]
    ):
        raise LabViolation("Dedicated PostgreSQL cluster identity mismatch")


def service_plan(root):
    root = plain(root)
    m = check(root)
    release = root / "releases" / m["active_sha"]
    return dict(
        name=m["services"]["app"],
        dependency=m["services"]["postgres"],
        host=str(root / "dependencies" / "service" / "pythonservice.exe"),
        python_class=str(root / "dependencies/service/lab_service_bootstrap") + ".LabService",
        account=ACCOUNT,
        installation_id=m["installation_id"],
        root=str(root),
        release=str(release),
        source_sha=m["active_sha"],
        python=str(root / "dependencies/venv/Scripts/python.exe"),
    )


def pg_plan(root):
    root = plain(root)
    p = service_plan(root)
    binary = Path(r"C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe")
    return dict(
        name=p["dependency"],
        account=ACCOUNT,
        installation_id=p["installation_id"],
        root=str(root),
        image=subprocess.list2cmdline(
            [
                str(binary),
                "runservice",
                "-N",
                p["dependency"],
                "-D",
                str(root / "postgres-data"),
                "-w",
            ]
        ),
    )


def owned(current, plan, image):
    if current is None:
        return
    if (
        current.get("image") != image
        or current.get("installation_id") != plan["installation_id"]
        or current.get("root") != plan["root"]
        or current.get("account", "").casefold() != plan.get("account", ACCOUNT).casefold()
        or ("source_sha" in plan and current.get("source_sha") != plan["source_sha"])
        or (
            plan.get("enforce_python_class")
            and current.get("python_class") != plan.get("python_class")
        )
        or (
            plan.get("executable")
            and current.get("state") == "running"
            and str(current.get("executable", "")).casefold() != str(plan["executable"]).casefold()
        )
    ):
        raise LabViolation("SCM registration is foreign or has an unexpected image/account")


def preflight(root):
    root = plain(root)
    plan = service_plan(root)
    runtime_manifest = json.loads(plain(root / "state/service-runtime.json").read_text())
    verify_tree(root / "dependencies/service", runtime_manifest["files"])
    if not plain(plan["python"]).is_file() or not plain(plan["host"]).is_file():
        raise LabViolation("Pinned private Python service runtime required")
    pg_manifest = json.loads(plain(root / "state/postgres-binaries.json").read_text())
    verify_tree(Path(r"C:\Program Files\PostgreSQL\18\bin"), pg_manifest["files"])
    validate_pg_identity(root)
    config = json.loads(plain(root / "config.json").read_text())
    from psycopg.conninfo import conninfo_to_dict

    pg = conninfo_to_dict(config["database_url"])
    if (
        config.get("host") != "127.0.0.1"
        or config.get("port") != 18766
        or plain(config["data_dir"]) != root / "runtime"
        or plain(config["frontend_dir"]) != Path(plan["release"]) / "frontend"
        or pg.get("host") != "127.0.0.1"
        or pg.get("port") != "55470"
        or pg.get("service")
        or pg.get("hostaddr")
        or not pg.get("dbname")
        or pg.get("dbname") in {"postgres", "template0", "template1"}
    ):
        raise LabViolation("Configuration must bind dedicated loopback API18766/PG55470")
    pg_config = plain(root / "postgres-data/postgresql.conf").read_text()
    validate_pg_config(pg_config)
    auto = plain(root / "postgres-data/postgresql.auto.conf")
    if auto.exists() and any(
        line.split("#", 1)[0].strip() for line in auto.read_text().splitlines()
    ):
        raise LabViolation("PostgreSQL ALTER SYSTEM overrides are forbidden")
    return plan


def apply(action, root, scm, ready=preflight):
    if action not in {"install", "start", "stop", "restart", "status", "remove"}:
        raise LabViolation("Unknown SCM action")
    root = plain(root)
    p, pg = service_plan(root), pg_plan(root)
    image = subprocess.list2cmdline([p["host"]])
    app_state, pg_state = scm.inspect(p["name"]), scm.inspect(pg["name"])
    owned(app_state, p, image)
    owned(pg_state, pg, pg["image"])
    if action == "install":
        if app_state is not None or pg_state is not None:
            raise LabViolation("Fresh install requires both UUID services absent")
        ready(root)
        journal = plain(root / "state/scm-install.json")
        journal.write_text(
            json.dumps(
                dict(
                    installation_id=p["installation_id"],
                    source_sha=p["source_sha"],
                    phase="registering-postgres",
                )
            ),
            encoding="utf-8",
        )
        scm.install(pg, pg["image"], None)
        journal.write_text(
            json.dumps(
                dict(
                    installation_id=p["installation_id"],
                    source_sha=p["source_sha"],
                    phase="registering-app",
                )
            ),
            encoding="utf-8",
        )
        scm.install(p, image, p["dependency"])
        journal.write_text(
            json.dumps(
                dict(
                    installation_id=p["installation_id"],
                    source_sha=p["source_sha"],
                    phase="registered",
                )
            ),
            encoding="utf-8",
        )
    elif action == "status":
        pass
    else:
        if action != "remove" and (app_state is None or pg_state is None):
            raise LabViolation("Both owned registrations required")
        if action in {"start", "restart"}:
            ready(root)
        if action in {"stop", "restart"}:
            scm.stop(p["name"])
            scm.wait(p["name"], "stopped")
            scm.stop(pg["name"])
            scm.wait(pg["name"], "stopped")
        if action in {"start", "restart"}:
            scm.start(pg["name"])
            scm.wait(pg["name"], "running")
            scm.start(p["name"])
            scm.wait(p["name"], "running")
        if action == "remove":
            if any(s is not None and s["state"] != "stopped" for s in (app_state, pg_state)):
                raise LabViolation("Stop both owned services before deregistering")
            if app_state is not None:
                scm.remove(p["name"])
            if pg_state is not None:
                scm.remove(pg["name"])
    evidence_path = plain(root / "runtime/service.json")
    try:
        evidence = json.loads(evidence_path.read_text()) if evidence_path.exists() else None
    except PermissionError:
        evidence = {
            "available": False,
            "reason": "runtime evidence requires data-directory read access",
        }
    return dict(
        action=action,
        source_sha=p["source_sha"],
        installation_id=p["installation_id"],
        app=scm.inspect(p["name"]),
        postgres=scm.inspect(pg["name"]),
        runtime_evidence=evidence,
    )


class NativeSCM:
    def __init__(self, mutating=False):
        import win32service as ws

        self.ws = ws
        self._bound = None
        self._bound_plans = {}
        self.manager = ws.OpenSCManager(
            None, None, ws.SC_MANAGER_CONNECT | (ws.SC_MANAGER_CREATE_SERVICE if mutating else 0)
        )

    def close(self):
        self.ws.CloseServiceHandle(self.manager)

    def inspect(self, name):
        if getattr(self, "_bound", None) is not None and self._bound.get(name) is not None:
            return self._inspect_handle(name, self._bound[name])
        ws = self.ws
        try:
            h = ws.OpenService(
                self.manager, name, ws.SERVICE_QUERY_CONFIG | ws.SERVICE_QUERY_STATUS
            )
        except Exception as exc:
            if getattr(exc, "winerror", None) == 1060:
                return None
            raise
        try:
            return self._inspect_handle(name, h)
        finally:
            ws.CloseServiceHandle(h)

    def _inspect_handle(self, name, h):
        import winreg

        ws = self.ws
        c, status = ws.QueryServiceConfig(h), ws.QueryServiceStatusEx(h)
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                "SYSTEM\\CurrentControlSet\\Services\\" + name + "\\Parameters",
            ) as key:
                options = {
                    k: winreg.QueryValueEx(key, k)[0]
                    for k in ("InstallationId", "Root", "SourceSHA")
                }
        except OSError as exc:
            raise LabViolation("Service has no fresh lab ownership parameters") from exc
        python_class = None
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                "SYSTEM\\CurrentControlSet\\Services\\" + name + "\\PythonClass",
            ) as key:
                python_class = winreg.QueryValueEx(key, None)[0]
        except FileNotFoundError:
            pass
        exited = False
        try:
            executable = self.process_image(status["ProcessId"]) if status["ProcessId"] else None
        except ProcessLookupError:
            executable = None
            exited = True  # SCM status snapshot raced an authenticated process exit.
        return dict(
            image=c[3],
            python_class=python_class,
            account=c[7],
            state="pending"
            if exited
            else {ws.SERVICE_STOPPED: "stopped", ws.SERVICE_RUNNING: "running"}.get(
                status["CurrentState"], "pending"
            ),
            pid=status["ProcessId"],
            executable=executable,
            installation_id=options["InstallationId"],
            root=options["Root"],
            source_sha=options["SourceSHA"],
        )

    @staticmethod
    def process_image(pid):
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:
                raise ProcessLookupError("Service process has exited")
            raise LabViolation("Cannot prove service executable identity")
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                kernel.GetExitCodeProcess.argtypes = [
                    wintypes.HANDLE,
                    ctypes.POINTER(wintypes.DWORD),
                ]
                exit_code = wintypes.DWORD()
                if (
                    kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                    and exit_code.value != 259
                ):
                    raise ProcessLookupError("Service process exited during image query")
                raise LabViolation("Cannot query service executable identity")
            return buffer.value
        finally:
            kernel.CloseHandle(handle)

    def lease(self, registrations):
        """Retain exact SCM objects: a deleted name cannot be reused while held."""
        from contextlib import contextmanager

        @contextmanager
        def held():
            if self._bound is not None:
                raise LabViolation("Nested SCM lifecycle lease refused")
            self._bound = {}
            self._bound_plans = {p["name"]: (p, image) for p, image in registrations}
            try:
                for plan, image in registrations:
                    try:
                        handle = self.ws.OpenService(
                            self.manager,
                            plan["name"],
                            self.ws.SERVICE_QUERY_CONFIG
                            | self.ws.SERVICE_QUERY_STATUS
                            | self.ws.SERVICE_CHANGE_CONFIG
                            | self.ws.SERVICE_START
                            | self.ws.SERVICE_STOP
                            | 0x10000,
                        )
                    except Exception as exc:
                        if getattr(exc, "winerror", None) != 1060:
                            raise
                        handle = None
                    self._bound[plan["name"]] = handle
                    if handle is not None:
                        owned(self._inspect_handle(plan["name"], handle), plan, image)
                yield
            finally:
                for handle in self._bound.values():
                    if handle is not None:
                        self.ws.CloseServiceHandle(handle)
                self._bound = None
                self._bound_plans = {}

        return held()

    def install(self, plan, image, dependency):
        import winreg

        ws = self.ws
        h = ws.CreateService(
            self.manager,
            plan["name"],
            plan["name"],
            ws.SERVICE_CHANGE_CONFIG
            | (
                (
                    ws.SERVICE_QUERY_CONFIG
                    | ws.SERVICE_QUERY_STATUS
                    | ws.SERVICE_STOP
                    | ws.SERVICE_START
                )
                if getattr(self, "_bound", None) is not None
                else 0
            )
            | 0x00010000
            | (ws.SERVICE_START if plan.get("recovery_delays_ms") else 0),
            ws.SERVICE_WIN32_OWN_PROCESS,
            ws.SERVICE_AUTO_START if plan.get("auto_start", False) else ws.SERVICE_DEMAND_START,
            ws.SERVICE_ERROR_NORMAL,
            image,
            None,
            0,
            [dependency] if dependency else None,
            plan.get("account", ACCOUNT),
            None,
        )
        try:
            if plan.get("service_sid", False):
                ws.ChangeServiceConfig2(
                    h, ws.SERVICE_CONFIG_SERVICE_SID_INFO, ws.SERVICE_SID_TYPE_UNRESTRICTED
                )
            if "failure_on_non_crash" in plan:
                ws.ChangeServiceConfig2(
                    h, ws.SERVICE_CONFIG_FAILURE_ACTIONS_FLAG, plan["failure_on_non_crash"]
                )
            if plan.get("recovery_delays_ms"):
                delays = plan["recovery_delays_ms"]
                if len(delays) != 3 or delays[-1] != 0:
                    raise LabViolation("Finite recovery budget required")
                ws.ChangeServiceConfig2(
                    h,
                    ws.SERVICE_CONFIG_FAILURE_ACTIONS,
                    {
                        "ResetPeriod": 86400,
                        "RebootMsg": "",
                        "Command": "",
                        "Actions": [
                            (ws.SC_ACTION_RESTART, delays[0]),
                            (ws.SC_ACTION_RESTART, delays[1]),
                            (ws.SC_ACTION_NONE, 0),
                        ],
                    },
                )
            base = "SYSTEM\\CurrentControlSet\\Services\\" + plan["name"]
            with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, base + "\\Parameters") as key:
                for k, v in {
                    "InstallationId": plan["installation_id"],
                    "Root": plan["root"],
                    "SourceSHA": plan.get("source_sha", ""),
                }.items():
                    winreg.SetValueEx(key, k, 0, winreg.REG_SZ, v)
            if plan.get("python_class"):
                with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, base + "\\PythonClass") as key:
                    winreg.SetValueEx(key, None, 0, winreg.REG_SZ, plan["python_class"])
                with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, base + "\\PythonPath") as key:
                    site = str(Path(plan["root"]) / "dependencies/venv/Lib/site-packages")
                    winreg.SetValueEx(
                        key,
                        None,
                        0,
                        winreg.REG_SZ,
                        ";".join(
                            [
                                str(Path(plan["root"]) / "dependencies/service"),
                                plan["release"],
                                site,
                                site + "\\win32",
                                site + "\\win32\\lib",
                            ]
                        ),
                    )
            if getattr(self, "_bound", None) is not None:
                self._bound[plan["name"]] = h
        except BaseException:
            # Only this exact CreateService handle is eligible for rollback.
            # Existing/foreign services can never reach this branch.
            ws.DeleteService(h)
            raise
        finally:
            if getattr(self, "_bound", None) is None or self._bound.get(plan["name"]) is not h:
                ws.CloseServiceHandle(h)

    def promote(self, initial, final, image):
        """Change the same stopped provisioning service into the runtime service.

        The start type follows ``final["auto_start"]``. The installer promotes to
        DEMAND_START and enables AUTO_START only after the install commit point, so
        an interrupted installation can never be started by SCM at boot.
        """
        import winreg

        ws = self.ws
        start_type = ws.SERVICE_AUTO_START if final.get("auto_start") else ws.SERVICE_DEMAND_START
        with self.lease([(initial, image)]):
            handle = self._bound[initial["name"]]
            if (
                handle is None
                or self._inspect_handle(initial["name"], handle)["state"] != "stopped"
            ):
                raise LabViolation("Stop the exact provisioning service before promotion")
            self._call(
                initial["name"],
                ws.SERVICE_CHANGE_CONFIG,
                lambda h: ws.ChangeServiceConfig(
                    h,
                    ws.SERVICE_NO_CHANGE,
                    start_type,
                    ws.SERVICE_NO_CHANGE,
                    None,
                    None,
                    0,
                    [final["dependency"]],
                    None,
                    None,
                    None,
                ),
            )
            ws.ChangeServiceConfig2(
                handle,
                ws.SERVICE_CONFIG_FAILURE_ACTIONS_FLAG,
                final.get("failure_on_non_crash", False),
            )
            delays = final["recovery_delays_ms"]
            ws.ChangeServiceConfig2(
                handle,
                ws.SERVICE_CONFIG_FAILURE_ACTIONS,
                dict(
                    ResetPeriod=86400,
                    RebootMsg="",
                    Command="",
                    Actions=[
                        (ws.SC_ACTION_RESTART, delays[0]),
                        (ws.SC_ACTION_RESTART, delays[1]),
                        (ws.SC_ACTION_NONE, 0),
                    ],
                ),
            )
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                "SYSTEM\\CurrentControlSet\\Services\\" + initial["name"] + "\\PythonClass",
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.SetValueEx(key, None, 0, winreg.REG_SZ, final["python_class"])
            self._bound_plans[initial["name"]] = (final, image)
            owned(self._inspect_handle(initial["name"], handle), final, image)

    def set_start_type(self, registrations, *, automatic):
        """Idempotently switch exact owned registrations between AUTO and DEMAND start.

        Every service is pinned and ownership-validated through the lease before its
        configuration changes; a missing or foreign registration changes nothing.
        """
        ws = self.ws
        start_type = ws.SERVICE_AUTO_START if automatic else ws.SERVICE_DEMAND_START
        with self.lease(registrations):
            if any(self._bound.get(plan["name"]) is None for plan, _ in registrations):
                raise LabViolation("Both owned registrations required to change start type")
            for plan, _ in registrations:
                self._call(
                    plan["name"],
                    ws.SERVICE_CHANGE_CONFIG,
                    lambda h: ws.ChangeServiceConfig(
                        h,
                        ws.SERVICE_NO_CHANGE,
                        start_type,
                        ws.SERVICE_NO_CHANGE,
                        None,
                        None,
                        0,
                        None,
                        None,
                        None,
                        None,
                    ),
                )

    def _call(self, name, access, fn):
        if getattr(self, "_bound", None) is not None and self._bound.get(name) is not None:
            h = self._bound[name]
            plan, image = self._bound_plans[name]
            owned(self._inspect_handle(name, h), plan, image)
            return fn(h)
        h = self.ws.OpenService(self.manager, name, access)
        try:
            return fn(h)
        finally:
            self.ws.CloseServiceHandle(h)

    def start(self, name):
        if self.inspect(name)["state"] == "stopped":
            self._call(name, self.ws.SERVICE_START, lambda h: self.ws.StartService(h, None))

    def stop(self, name):
        if self.inspect(name)["state"] != "stopped":
            self._call(
                name,
                self.ws.SERVICE_STOP,
                lambda h: self.ws.ControlService(h, self.ws.SERVICE_CONTROL_STOP),
            )

    def remove(self, name):
        self._call(
            name, 0x00010000, self.ws.DeleteService
        )  # Standard DELETE access, not a win32service constant.

    def wait(self, name, state):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.inspect(name)["state"] == state:
                return
            time.sleep(0.25)
        raise LabViolation("SCM transition timed out")


def stage_service_runtime(
    root,
    *,
    marker_reader=check,
    bootstrap="lab_service_bootstrap.py",
    site_override=None,
    transaction=None,
    precompile=True,
):
    """Stage a private SCM host from already locked bootstrap dependencies, never user Python.

    The resulting byte inventory is an integrity receipt. Bootstrap must independently
    verify Python/wheel provenance before this call; the receipt is not a trust anchor.
    """
    import compileall
    import hashlib
    import py_compile
    import shutil

    root = plain(root)
    marker = marker_reader(root)
    base = plain(root / "dependencies/python")
    site = plain(
        root / "dependencies/venv/Lib/site-packages" if site_override is None else site_override
    )
    destination = plain(root / "dependencies/service")
    if destination.exists() or (root / "state/service-runtime.json").exists():
        raise LabViolation("Private service runtime already exists; never overwrite")
    # Enumerate and reject every reparse descendant before copying any bytes.
    for source in (base, site):
        for item in source.rglob("*"):
            plain(item)
    dlls = list(base.glob("python3[0-9]*.dll"))
    if len(dlls) != 1 or not (base / "Lib").is_dir():
        raise LabViolation("Private complete CPython runtime required")
    suffix = dlls[0].stem.removeprefix("python")
    host = site / "win32/pythonservice.exe"
    types = site / "pywin32_system32" / ("pywintypes" + suffix + ".dll")
    manager = site / "win32/servicemanager.pyd"
    if not host.is_file() or not types.is_file() or not manager.is_file():
        raise LabViolation(
            "Pinned compatible pywin32 host, service manager and native types DLL required"
        )
    copytree = shutil.copytree if transaction is None else transaction.copytree
    copyfile = shutil.copy2 if transaction is None else transaction.copyfile
    copytree(base, destination, ignore=shutil.ignore_patterns("site-packages", "__pycache__"))
    copyfile(host, destination / "pythonservice.exe")
    copyfile(types, destination / types.name)
    # pythonservice imports this before the service-specific PythonPath is available.
    copyfile(manager, destination / manager.name)
    # Native pywin32 does not read the service-specific PythonPath registry key.
    # Plain .pth paths are processed by CPython before loading PythonClass.
    paths = [
        destination,
        root / "releases" / marker.get("active_sha", marker.get("source_sha")),
        site,
        site / "win32",
        site / "win32/lib",
    ]
    packages = destination / "Lib/site-packages"
    if transaction is None:
        packages.mkdir(exist_ok=True)
        (packages / "tester-lab.pth").write_text(
            "".join(str(path) + "\n" for path in paths), encoding="utf-8"
        )
    else:
        transaction.mkdir(packages)
        transaction.write_bytes(
            packages / "tester-lab.pth", "".join(str(path) + "\n" for path in paths).encode("utf-8")
        )
    startup = Path(__file__).with_name("lab_sitecustomize.py")
    copyfile(startup, destination / "Lib/sitecustomize.py")
    copyfile(
        Path(__file__).with_name(bootstrap),
        destination / bootstrap,
    )
    # Cache both startup modules: disabling bytecode during execution can still
    # cache the disabling module itself. Bootstrap Python must match private Python.
    if precompile and not compileall.compile_dir(str(destination / "Lib"), quiet=2):
        raise LabViolation("Private service standard library precompilation failed")
    if precompile:
        py_compile.compile(str(destination / bootstrap), doraise=True)
    files = {
        p.relative_to(destination).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in destination.rglob("*")
        if p.is_file()
    }
    manifest = dict(schema_version="tester-lab-service-runtime/v1", files=files)
    if transaction is None:
        with (root / "state/service-runtime.json").open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2)
    else:
        transaction.write_bytes(
            root / "state/service-runtime.json", json.dumps(manifest, indent=2).encode("utf-8")
        )
    return manifest
