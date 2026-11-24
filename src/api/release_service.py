"""Release lifecycle on the existing native SCM backend and supervisor host."""

import subprocess
from pathlib import Path

from .lab_scm import NativeSCM, owned
from .lab_service import LabService
from .release_install import lifecycle_lock, load_marker


def plans(marker):
    root = Path(marker["root"])
    common = dict(
        root=str(root),
        installation_id=marker["installation_id"],
        source_sha=marker["source_sha"],
        auto_start=True,
        service_sid=True,
        recovery_delays_ms=[1000, 5000, 0],
        failure_on_non_crash=True,
    )
    app = dict(
        common,
        name=marker["services"]["app"],
        account="NT SERVICE\\" + marker["services"]["app"],
        dependency=marker["services"]["postgres"],
        host=str(root / "dependencies/service/pythonservice.exe"),
        executable=str(root / "dependencies/service/pythonservice.exe"),
        enforce_python_class=True,
        python_class=str(root / "dependencies/service/release_service_bootstrap")
        + ".ReleaseService",
        release=str(root / "releases" / marker["source_sha"]),
        python=str(root / "dependencies/venv/Scripts/python.exe"),
    )
    pg = dict(
        common,
        name=marker["services"]["postgres"],
        account="NT SERVICE\\" + marker["services"]["postgres"],
        host=str(root / "dependencies/service/pythonservice.exe"),
        executable=str(root / "dependencies/service/pythonservice.exe"),
        enforce_python_class=True,
        python_class=str(root / "dependencies/service/release_service_bootstrap")
        + ".ReleasePostgresService",
        release=str(root / "releases" / marker["source_sha"]),
        python=str(root / "dependencies/venv/Scripts/python.exe"),
    )
    pg["image"] = subprocess.list2cmdline([pg["host"]])
    return app, pg


def validate_resources(marker, scm=None):
    local = scm is None
    scm = NativeSCM() if local else scm
    try:
        result = []
        for plan in plans(marker):
            image = plan.get("image", subprocess.list2cmdline([plan.get("host", "")]))
            current = scm.inspect(plan["name"])
            owned(current, plan, image)
            result.append(current)
        return result
    finally:
        if local:
            scm.close()


def service_sid(name):
    """Windows service SID derivation remains stable after SCM deregistration."""
    import hashlib
    import struct

    digest = hashlib.sha1(name.upper().encode("utf-16le"), usedforsecurity=False).digest()
    return "S-1-5-80-" + "-".join(str(n) for n in struct.unpack("<5I", digest))


def job_name(marker, role="app"):
    prefix = "TesterReleaseJob_" if role == "app" else "TesterReleasePostgresJob_"
    return "Global\\" + prefix + marker["installation_id"].replace("-", "")


def create_owned_job(marker, role="app"):
    import pywintypes
    import win32api
    import win32job
    import win32security

    sid = win32security.ConvertStringSidToSid(service_sid(marker["services"][role]))
    acl = win32security.ACL()
    for value in (
        sid,
        win32security.ConvertStringSidToSid("S-1-5-18"),
        win32security.ConvertStringSidToSid("S-1-5-32-544"),
    ):
        acl.AddAccessAllowedAce(win32security.ACL_REVISION, 0x1F003F, value)
    descriptor = win32security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorOwner(sid, False)
    descriptor.SetSecurityDescriptorDacl(True, acl, False)
    descriptor.SetSecurityDescriptorControl(
        win32security.SE_DACL_PROTECTED, win32security.SE_DACL_PROTECTED
    )
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = descriptor
    handle = win32job.CreateJobObject(attributes, job_name(marker, role))
    if win32api.GetLastError() == 183:
        handle.Close()
        raise ValueError("Owned TESTER supervisor job already exists")
    _validate_job_owner(handle, marker, role)
    return handle


def _validate_job_owner(handle, marker, role="app"):
    import win32security

    descriptor = win32security.GetSecurityInfo(
        handle,
        win32security.SE_KERNEL_OBJECT,
        win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION,
    )
    expected = {service_sid(marker["services"][role]), "S-1-5-18", "S-1-5-32-544"}
    if win32security.ConvertSidToStringSid(descriptor.GetSecurityDescriptorOwner()) != service_sid(
        marker["services"][role]
    ):
        raise ValueError("Foreign TESTER supervisor job owner")
    acl = descriptor.GetSecurityDescriptorDacl()
    actual = set()
    for index in range(acl.GetAceCount()):
        (kind, flags), mask, sid = acl.GetAce(index)
        if kind != 0 or flags != 0 or mask != 0x1F003F:
            raise ValueError("Foreign TESTER supervisor job access")
        actual.add(win32security.ConvertSidToStringSid(sid))
    if actual != expected or acl.GetAceCount() != 3:
        raise ValueError("Unexpected TESTER supervisor job principals")


def stop_and_prove_exit(marker):
    with capture_stop_proof(marker) as proof:
        with NativeController(marker) as controller:
            states = controller.apply("stop")
        proof.prove_exit()
        return states


def _process_parents():
    """Read process ancestry only; never open or query tokens of unrelated PPLs."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    class Entry(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD),
            ("usage", wintypes.DWORD),
            ("pid", wintypes.DWORD),
            ("heap", ctypes.c_size_t),
            ("module", wintypes.DWORD),
            ("threads", wintypes.DWORD),
            ("parent", wintypes.DWORD),
            ("priority", wintypes.LONG),
            ("flags", wintypes.DWORD),
            ("exe", wintypes.WCHAR * 260),
        ]

    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    result = {}
    try:
        entry = Entry()
        entry.size = ctypes.sizeof(entry)
        more = kernel.Process32FirstW(handle, ctypes.byref(entry))
        while more:
            result[entry.pid] = entry.parent
            more = kernel.Process32NextW(handle, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)
    return result


def _same_executable_file(actual, expected):
    """Compare physical Windows file identity, including legitimate OS aliases."""
    import win32file

    handles = []
    try:
        for path in (actual, expected):
            handles.append(
                win32file.CreateFile(
                    str(path),
                    0x80,
                    1 | 2 | 4,
                    None,
                    3,
                    0x00200000,
                    None,
                )
            )
        info = [win32file.GetFileInformationByHandle(handle) for handle in handles]
        if any(row[0] & 0x400 for row in info):
            return False  # Never follow reparse points as executable authority.
        return (info[0][4], info[0][8], info[0][9]) == (info[1][4], info[1][8], info[1][9])
    finally:
        for handle in handles:
            handle.Close()


def _owned_postgres_console(handle, image, parent, postgres_image, snapshot_started):
    import win32process

    from .release_install import system_binary

    if not _same_executable_file(image, system_binary("conhost.exe")):
        return False
    if not _same_executable_file(parent["image"], postgres_image):
        return False
    child_created = win32process.GetProcessTimes(handle)["CreationTime"].timestamp()
    parent_created = win32process.GetProcessTimes(parent["handle"])["CreationTime"].timestamp()
    # PPID is historical: reject a child predating its currently held parent.
    # A post-snapshot child could be a reused PID, so it is never adopted here.
    return parent_created <= child_created <= snapshot_started


class NativeStopProof:
    """Stable handles for authenticated service roots, PG descendants and app job."""

    def __init__(self, marker, *, validator=validate_resources, pg_descendants=True):
        import win32job

        self.handles = []
        self.processes = {}
        self.job = None
        self.pg_job = None
        try:
            states = validator(marker)
            root = Path(marker["root"])
            pg_roots = set()
            for role, state, plan in zip(("app", "postgres"), states, plans(marker), strict=True):
                if state and state.get("pid"):
                    self._capture(
                        state["pid"], service_sid(marker["services"][role]), {plan["executable"]}
                    )
                    if role == "postgres":
                        pg_roots.add(state["pid"])
            try:
                self.job = win32job.OpenJobObject(0x20000 | 4 | 8, False, job_name(marker))
            except Exception as exc:
                if getattr(exc, "winerror", None) != 2 or (
                    states[0] and states[0]["state"] == "running"
                ):
                    raise
            if self.job is not None:
                _validate_job_owner(self.job, marker)
            if pg_descendants:
                try:
                    self.pg_job = win32job.OpenJobObject(
                        0x20000 | 4 | 8, False, job_name(marker, "postgres")
                    )
                except Exception as exc:
                    if getattr(exc, "winerror", None) != 2 or (
                        states[1] and states[1]["state"] == "running"
                    ):
                        raise
                if self.pg_job is not None:
                    _validate_job_owner(self.pg_job, marker, "postgres")
            pidfile = root / "postgres-data/postmaster.pid"
            if pg_descendants and pidfile.exists():
                from .lab_install import plain

                lines = plain(pidfile).read_text().splitlines()
                if (
                    len(lines) < 4
                    or plain(lines[1]) != root / "postgres-data"
                    or lines[3] != str(marker["ports"]["postgres"])
                ):
                    raise ValueError("Dedicated PostgreSQL PID receipt scope mismatch")
                pg_roots.add(int(lines[0]))
            if not pg_descendants:
                pg_roots.clear()
            import time

            snapshot_started = time.time()
            parents = _process_parents()
            descendants = set(pg_roots)
            while True:
                more = {pid for pid, parent in parents.items() if parent in descendants}
                if more <= descendants:
                    break
                descendants.update(more)
            allowed = {
                str(root / "dependencies/postgres/bin/postgres.exe"),
                str(root / "dependencies/postgres/bin/pg_ctl.exe"),
            }
            # Authenticate parents before considering their console children.
            pending = set(descendants)
            while pending:
                ready = {
                    pid for pid in pending if pid in pg_roots or parents.get(pid) not in pending
                }
                if not ready:
                    raise ValueError("Cyclic PostgreSQL ancestry snapshot")
                for pid in ready:
                    if pid in self.processes:
                        continue  # SCM host already authenticated with its role-specific image.
                    try:
                        self._capture(
                            pid,
                            service_sid(marker["services"]["postgres"]),
                            allowed,
                            console_parent=self.processes.get(parents.get(pid)),
                            postgres_image=root / "dependencies/postgres/bin/postgres.exe",
                            snapshot_started=snapshot_started,
                        )
                    except ValueError as exc:
                        ancestry = {child: parents.get(child) for child in sorted(descendants)}
                        raise ValueError(
                            f"PostgreSQL stop-proof rejected: roots={sorted(pg_roots)}; "
                            f"ancestry={ancestry}; {exc}"
                        ) from exc
                pending.difference_update(ready)
        except BaseException:
            self.close()
            raise

    def _capture(
        self, pid, sid, allowed, *, console_parent=None, postgres_image=None, snapshot_started=None
    ):
        import ctypes
        from ctypes import wintypes

        import win32api
        import win32con
        import win32security

        try:
            handle = win32api.OpenProcess(0x100000 | 0x1000, False, pid)
        except Exception as exc:
            if getattr(exc, "winerror", None) == 87:
                return
            raise
        token = None
        try:
            token = win32security.OpenProcessToken(handle, win32con.TOKEN_QUERY)
            actual = win32security.ConvertSidToStringSid(
                win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            )
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.QueryFullProcessImageNameW.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.LPWSTR,
                ctypes.POINTER(wintypes.DWORD),
            ]
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            if not kernel.QueryFullProcessImageNameW(int(handle), 0, buffer, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            executable_allowed = buffer.value.casefold() in {path.casefold() for path in allowed}
            if actual == sid and not executable_allowed and console_parent is not None:
                executable_allowed = _owned_postgres_console(
                    handle, buffer.value, console_parent, postgres_image, snapshot_started
                )
            if actual != sid or not executable_allowed:
                raise ValueError(
                    "Foreign executable/token in owned service ancestry: "
                    f"pid={pid}; image={buffer.value!r}; token_user={actual}; "
                    f"expected_token_user={sid}; allowed_images={sorted(allowed)!r}"
                )
            self.handles.append(handle)
            if not hasattr(self, "processes"):
                self.processes = {}
            self.processes[pid] = dict(handle=handle, image=buffer.value, sid=actual)
            handle = None
        finally:
            if token is not None:
                token.Close()
            if handle is not None:
                handle.Close()

    def prove_exit(self):
        import time

        import win32event
        import win32job

        for job in (self.job, self.pg_job):
            if job is None:
                continue
            if win32job.QueryInformationJobObject(
                job, win32job.JobObjectBasicAccountingInformation
            )["ActiveProcesses"]:
                win32job.TerminateJobObject(job, 1)
            deadline = time.monotonic() + 15
            while win32job.QueryInformationJobObject(
                job, win32job.JobObjectBasicAccountingInformation
            )["ActiveProcesses"]:
                if time.monotonic() >= deadline:
                    raise ValueError("Owned supervisor job did not empty")
                time.sleep(0.1)
        for handle in self.handles:
            if win32event.WaitForSingleObject(handle, 15000) != win32event.WAIT_OBJECT_0:
                raise ValueError("Authenticated TESTER process survived service stop")

    def close(self):
        if self.job is not None:
            self.job.Close()
            self.job = None
        if self.pg_job is not None:
            self.pg_job.Close()
            self.pg_job = None
        for handle in self.handles:
            handle.Close()
        self.handles = []


def capture_stop_proof(marker):
    from contextlib import contextmanager

    @contextmanager
    def lease():
        proof = NativeStopProof(marker)
        try:
            yield proof
        finally:
            proof.close()

    return lease()


def remove_resources(marker):
    with NativeController(marker) as controller:
        return controller.apply("remove")


# Upper bounds for an operator start: PostgreSQL readiness may include crash recovery;
# the App host may wait for restart authority before its own readiness deadline.
PG_START_SECONDS = 660
APP_START_SECONDS = 180


class NativeController:
    def __init__(self, marker):
        self.marker = marker
        self.scm = NativeSCM(mutating=True)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.scm.close()

    def apply(self, action):
        from contextlib import nullcontext

        registrations = [
            (plan, plan.get("image", subprocess.list2cmdline([plan.get("host", "")])))
            for plan in plans(self.marker)
        ]
        lease = self.scm.lease(registrations) if hasattr(self.scm, "lease") else nullcontext()
        with lease:
            return self._apply(action)

    def _apply(self, action):
        app, pg = plans(self.marker)
        current = validate_resources(self.marker, self.scm)
        if action == "install":
            for plan, state in zip((pg, app), reversed(current), strict=True):
                if state is None:
                    self.scm.install(
                        plan,
                        plan.get("image", subprocess.list2cmdline([plan.get("host", "")])),
                        plan.get("dependency"),
                    )
        elif action in {"stop", "restart", "remove"}:
            for plan, state in zip((app, pg), current, strict=True):
                if state is not None:
                    validate_resources(self.marker, self.scm)
                    self.scm.stop(plan["name"])
                    self.scm.wait(plan["name"], "stopped")
            if action == "remove":
                for plan, state in zip((app, pg), current, strict=True):
                    if state is not None:
                        validate_resources(self.marker, self.scm)
                        self.scm.remove(plan["name"])
        elif action not in {"start", "status"}:
            raise ValueError("Unknown lifecycle action")
        if action in {"start", "restart"}:
            preflight(Path(self.marker["root"]))
            for plan, limit in ((pg, PG_START_SECONDS), (app, APP_START_SECONDS)):
                self.scm.start(plan["name"])
                self._wait_running(plan["name"], limit)
        return validate_resources(self.marker, self.scm)

    def _wait_running(self, name, limit):
        """The SCM wait is bounded; keep waiting only while the service is still pending."""
        import time

        from .lab_install import LabViolation

        deadline = time.monotonic() + limit
        while True:
            try:
                return self.scm.wait(name, "running")
            except LabViolation:
                if time.monotonic() >= deadline or self.scm.inspect(name)["state"] != "pending":
                    raise


def validate_config(marker, config):
    from psycopg.conninfo import conninfo_to_dict

    from .lab_install import plain

    root = Path(marker["root"])
    pg = conninfo_to_dict(config["database_url"])
    if (
        config.get("host") != "127.0.0.1"
        or config.get("port") != marker["ports"]["api"]
        or plain(config["data_dir"]) != root / "runtime"
        or plain(config["frontend_dir"]) != root / "releases" / marker["source_sha"] / "frontend"
        or pg.get("host") != "127.0.0.1"
        or pg.get("port") != str(marker["ports"]["postgres"])
        or pg.get("port") == "5432"
        or pg.get("dbname") != "tester"
        or pg.get("user") != "tester_app"
        or pg.get("require_auth") != "scram-sha-256"
        or pg.get("hostaddr") not in (None, "127.0.0.1")
        or any(key in pg for key in ("service", "options"))
    ):
        raise ValueError("Dedicated release API/database/storage scope required")


def preflight(root, *, physical_postgres=True, api_config=True):
    marker = load_marker(root)
    import json

    from .lab_install import verify_tree

    if api_config:
        config = json.loads((root / "config.json").read_text())
        validate_config(marker, config)
    from .lab_install import plain
    from .lab_scm import validate_pg_config

    if physical_postgres:
        validate_pg_config(
            plain(root / "postgres-data/postgresql.conf").read_text(),
            expected_port=marker["ports"]["postgres"],
        )
        auto = plain(root / "postgres-data/postgresql.auto.conf")
        if auto.exists() and any(
            line.split("#", 1)[0].strip() for line in auto.read_text().splitlines()
        ):
            raise ValueError("PostgreSQL ALTER SYSTEM overrides are forbidden")
    for name in ("service", "postgres"):
        receipt = json.loads((root / "state" / f"{name}-runtime.json").read_text())
        verify_tree(root / "dependencies" / name, receipt["files"])
    receipt = json.loads((root / "state/source-runtime.json").read_text())
    verify_tree(root / "releases" / marker["source_sha"], receipt["files"])
    if physical_postgres:
        validate_cluster_identity(marker)
    return marker


def validate_cluster_identity(marker):
    import json
    import os
    import re

    root = Path(marker["root"])
    result = subprocess.run(
        [
            str(root / "dependencies/postgres/bin/pg_controldata.exe"),
            "-D",
            str(root / "postgres-data"),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
        env=dict(os.environ, LANG="C", LC_MESSAGES="C"),
    )
    actual = re.findall(r"^Database system identifier:\s*(\d+)\s*$", result.stdout, re.MULTILINE)
    recorded = json.loads((root / "state/postgres-identity.json").read_text())
    if len(actual) != 1 or recorded != dict(
        installation_id=marker["installation_id"], system_identifier=actual[0]
    ):
        raise ValueError("Dedicated PostgreSQL cluster identity mismatch")


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=("install", "start", "stop", "restart", "status", "remove")
    )
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    marker = load_marker(args.root)
    with lifecycle_lock(args.root), NativeController(marker) as controller:
        print(controller.apply(args.action))


class ReleaseService(LabService):
    import threading

    _receipt_lock = threading.Lock()
    marker_reader = staticmethod(load_marker)

    # PostgreSQL may still be finishing startup/crash recovery after its own service
    # reports RUNNING. Waiting here keeps a transient outage from consuming one of the
    # two SCM recovery actions; exhausted/disabled policy still stops normally.
    admission_grace_seconds = 45

    def admit_start(self, root):
        import time

        from .restart_budget import BudgetDenied, BudgetUnavailable, DatabaseRestartBudget

        marker = load_marker(root)
        deadline = time.monotonic() + self.admission_grace_seconds
        try:
            budget = DatabaseRestartBudget.from_config(
                root / "config.json",
                root / "runtime",
                marker["installation_id"],
                marker["source_sha"],
            )
            while True:
                try:
                    budget.admit("host")
                    break
                except BudgetUnavailable:
                    if time.monotonic() >= deadline:
                        raise
                    if self._admission_pause():
                        return False  # Stop requested while authority was unavailable.
        except BudgetUnavailable:
            raise
        except BudgetDenied:
            return False
        self._restart_budget = budget
        return True

    def _admission_pause(self):
        import time

        event = getattr(self, "stop_event", None)
        if event is None:
            time.sleep(2)
            return False
        import win32event
        import win32service

        self.ReportServiceStatus(win32service.SERVICE_START_PENDING, waitHint=60000)
        return win32event.WaitForSingleObject(event, 2000) == win32event.WAIT_OBJECT_0

    def record_clean_stop(self, root):
        budget = getattr(self, "_restart_budget", None)
        if budget is not None:
            budget.record_clean_stop()  # Exact host-run token; children never hold it.

    def create_job(self, marker):
        return create_owned_job(marker)

    def record_phase(self, phase):
        import json
        import time

        from .release_install import write_json

        marker = load_marker(self.root)
        path = self.root / "runtime/service-lifecycle.json"
        with self._receipt_lock:
            previous = json.loads(path.read_text()) if path.exists() else {}
            rows = previous.get("transitions", [])[-63:]
            rows.append({"phase": phase, "at": time.time(), "instance_id": self.instance})
            write_json(
                path,
                dict(
                    source_sha=marker["source_sha"],
                    installation_id=marker["installation_id"],
                    instance_id=self.instance,
                    transitions=rows,
                ),
            )

    def service_preflight(self, root):
        # The App account must never read PostgreSQL raw files. The privileged
        # controller checks physical cluster identity; the service validates code,
        # dedicated DSNs and the protected SQL admission authority before launch.
        return preflight(root, physical_postgres=False)

    def health_port(self, marker):
        return marker["ports"]["api"]

    def health_ready(self, response, health, instance):
        return (
            response.status in (200, 503)
            and health.get("instance_id") == instance
            and all(health.get(key) == "ok" for key in ("database", "worker", "storage"))
        )

    @staticmethod
    def contract_builder(root):
        marker = load_marker(root)
        import os

        env = {
            k: v for k, v in os.environ.items() if not k.upper().startswith(("TESTER_", "PYTHON"))
        }
        env.update(
            TESTER_CONFIG_FILE=str(root / "config.json"),
            PYTHONNOUSERSITE="1",
            PYTHONDONTWRITEBYTECODE="1",
            GUARDIANS_ENABLED="false",
            TESTER_RESTART_BUDGET_DIR=str(root / "runtime"),
            TESTER_INSTALLATION_ID=marker["installation_id"],
            TESTER_SOURCE_SHA=marker["source_sha"],
        )
        return dict(
            argv=[str(root / "dependencies/venv/Scripts/python.exe"), "-m", "src.api.supervisor"],
            cwd=str(root / "releases" / marker["source_sha"]),
            env=env,
            sha=marker["source_sha"],
        )


class ReleasePostgresService(ReleaseService):
    """The existing SCM host owns foreground PostgreSQL and its entire job."""

    service_role = "postgres"
    uses_http_health = False
    # Crash recovery after power loss/unclean shutdown can exceed the App's 55 s.
    # The child must stay alive and in our job; START_PENDING keeps advancing.
    startup_deadline_seconds = 600

    def admit_start(self, root):
        return True  # PostgreSQL must be available before SQL admission is possible.

    def create_job(self, marker):
        return create_owned_job(marker, "postgres")

    def evidence_path(self, root):
        return root / "postgres-data/service.json"

    def signal_stop(self, root):
        pass  # SCM handler only signals; verified pg_ctl runs in the service thread.

    def graceful_stop(self, root):
        preflight(root, api_config=False)
        result = subprocess.run(
            [
                str(root / "dependencies/postgres/bin/pg_ctl.exe"),
                "-D",
                str(root / "postgres-data"),
                "-m",
                "fast",
                "-w",
                "-t",
                "25",
                "stop",
            ],
            capture_output=True,
            timeout=28,
            check=False,
        )
        if result.returncode:
            raise ValueError("Dedicated PostgreSQL graceful stop failed")

    def service_preflight(self, root):
        return preflight(root, api_config=False)

    def record_phase(self, phase):
        import json
        import time

        from .release_install import write_json

        marker = load_marker(self.root)
        path = self.root / "postgres-data/service-lifecycle.json"
        previous = json.loads(path.read_text()) if path.exists() else {}
        rows = previous.get("transitions", [])[-63:]
        rows.append(dict(phase=phase, at=time.time(), instance_id=self.instance))
        write_json(
            path,
            dict(
                source_sha=marker["source_sha"],
                installation_id=marker["installation_id"],
                transitions=rows,
            ),
        )

    def ready_without_http(self, root, marker, instance):
        result = subprocess.run(
            [
                str(root / "dependencies/postgres/bin/pg_isready.exe"),
                "-h",
                "127.0.0.1",
                "-p",
                str(marker["ports"]["postgres"]),
                "-t",
                "1",
            ],
            capture_output=True,
            timeout=3,
            check=False,
        )
        if result.returncode:
            return False
        lines = (root / "postgres-data/postmaster.pid").read_text().splitlines()
        if (
            len(lines) < 4
            or Path(lines[1]) != root / "postgres-data"
            or lines[3] != str(marker["ports"]["postgres"])
        ):
            raise ValueError("Foreground PostgreSQL PID scope mismatch")
        # Kernel-created process is assigned to our job before it can execute.
        # Its exact pid is also checked by the host readiness hook below.
        return int(lines[0]) == self.child_pid

    @staticmethod
    def contract_builder(root):
        import os

        marker = load_marker(root)
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.upper().startswith(("TESTER_", "PYTHON", "PG"))
        }
        env.update(PYTHONDONTWRITEBYTECODE="1", GUARDIANS_ENABLED="false")
        return dict(
            argv=[
                str(root / "dependencies/postgres/bin/postgres.exe"),
                "-D",
                str(root / "postgres-data"),
            ],
            cwd=str(root / "postgres-data"),
            env=env,
            sha=marker["source_sha"],
        )


if __name__ == "__main__":
    main()
