"""Explicit, scoped SCM controller. Imports have no native side effects."""

import json
import os
import re
import subprocess
import time
from pathlib import Path

from .lab_install import LabViolation, check, plain, verify_tree

ACCOUNT = "NT AUTHORITY\\LocalService"


def validate_pg_config(text):
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
    if values.get("port") != "55470" or values.get("listen_addresses") != "'127.0.0.1'":
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
        python_class="lab_service_bootstrap.LabService",
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
        or current.get("account", "").casefold() != ACCOUNT.casefold()
        or ("source_sha" in plan and current.get("source_sha") != plan["source_sha"])
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
        self.manager = ws.OpenSCManager(
            None, None, ws.SC_MANAGER_CONNECT | (ws.SC_MANAGER_CREATE_SERVICE if mutating else 0)
        )

    def close(self):
        self.ws.CloseServiceHandle(self.manager)

    def inspect(self, name):
        import winreg

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
            c, status = ws.QueryServiceConfig(h), ws.QueryServiceStatusEx(h)
        finally:
            ws.CloseServiceHandle(h)
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
        return dict(
            image=c[3],
            account=c[7],
            state={ws.SERVICE_STOPPED: "stopped", ws.SERVICE_RUNNING: "running"}.get(
                status["CurrentState"], "pending"
            ),
            pid=status["ProcessId"],
            installation_id=options["InstallationId"],
            root=options["Root"],
            source_sha=options["SourceSHA"],
        )

    def install(self, plan, image, dependency):
        import winreg

        ws = self.ws
        h = ws.CreateService(
            self.manager,
            plan["name"],
            plan["name"],
            ws.SERVICE_CHANGE_CONFIG,
            ws.SERVICE_WIN32_OWN_PROCESS,
            ws.SERVICE_DEMAND_START,
            ws.SERVICE_ERROR_NORMAL,
            image,
            None,
            0,
            [dependency] if dependency else None,
            ACCOUNT,
            None,
        )
        ws.CloseServiceHandle(h)
        base = "SYSTEM\\CurrentControlSet\\Services\\" + plan["name"]
        with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, base + "\\Parameters") as key:
            for k, v in {
                "InstallationId": plan["installation_id"],
                "Root": plan["root"],
                "SourceSHA": plan.get("source_sha", ""),
            }.items():
                winreg.SetValueEx(key, k, 0, winreg.REG_SZ, v)
        if dependency:
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

    def _call(self, name, access, fn):
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


def stage_service_runtime(root):
    """Stage a private SCM host from already locked bootstrap dependencies, never user Python.

    The resulting byte inventory is an integrity receipt. Bootstrap must independently
    verify Python/wheel provenance before this call; the receipt is not a trust anchor.
    """
    import compileall
    import hashlib
    import py_compile
    import shutil

    root = plain(root)
    check(root)
    base = plain(root / "dependencies/python")
    site = plain(root / "dependencies/venv/Lib/site-packages")
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
    if not host.is_file() or not types.is_file():
        raise LabViolation("Pinned compatible pywin32 host and native types DLL required")
    shutil.copytree(
        base, destination, ignore=shutil.ignore_patterns("site-packages", "__pycache__")
    )
    shutil.copy2(host, destination / "pythonservice.exe")
    shutil.copy2(types, destination / types.name)
    startup = Path(__file__).with_name("lab_sitecustomize.py")
    shutil.copy2(startup, destination / "Lib/sitecustomize.py")
    shutil.copy2(
        Path(__file__).with_name("lab_service_bootstrap.py"),
        destination / "lab_service_bootstrap.py",
    )
    # Cache both startup modules: disabling bytecode during execution can still
    # cache the disabling module itself. Bootstrap Python must match private Python.
    if not compileall.compile_dir(str(destination / "Lib"), quiet=2):
        raise LabViolation("Private service standard library precompilation failed")
    py_compile.compile(str(destination / "lab_service_bootstrap.py"), doraise=True)
    files = {
        p.relative_to(destination).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in destination.rglob("*")
        if p.is_file()
    }
    manifest = dict(schema_version="tester-lab-service-runtime/v1", files=files)
    with (root / "state/service-runtime.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2)
    return manifest
