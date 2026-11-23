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
        executable=str(root / "dependencies/postgres/bin/pg_ctl.exe"),
    )
    pg["image"] = subprocess.list2cmdline(
        [
            str(root / "dependencies/postgres/bin/pg_ctl.exe"),
            "runservice",
            "-N",
            pg["name"],
            "-D",
            str(root / "postgres-data"),
            "-w",
        ]
    )
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


def job_name(marker):
    return "Global\\TesterReleaseJob_" + marker["installation_id"].replace("-", "")


def create_owned_job(marker):
    import pywintypes
    import win32api
    import win32job
    import win32security

    sid = win32security.ConvertStringSidToSid(service_sid(marker["services"]["app"]))
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
    handle = win32job.CreateJobObject(attributes, job_name(marker))
    if win32api.GetLastError() == 183:
        handle.Close()
        raise ValueError("Owned TESTER supervisor job already exists")
    _validate_job_owner(handle, marker)
    return handle


def _validate_job_owner(handle, marker):
    import win32security

    descriptor = win32security.GetSecurityInfo(
        handle,
        win32security.SE_KERNEL_OBJECT,
        win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION,
    )
    expected = {service_sid(marker["services"]["app"]), "S-1-5-18", "S-1-5-32-544"}
    if win32security.ConvertSidToStringSid(descriptor.GetSecurityDescriptorOwner()) != service_sid(
        marker["services"]["app"]
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


class NativeStopProof:
    """Stable handles for authenticated service roots, PG descendants and app job."""

    def __init__(self, marker, *, validator=validate_resources, pg_descendants=True):
        import win32job

        self.handles = []
        self.job = None
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
            for pid in descendants:
                self._capture(pid, service_sid(marker["services"]["postgres"]), allowed)
        except BaseException:
            self.close()
            raise

    def _capture(self, pid, sid, allowed):
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
            if actual != sid or buffer.value.casefold() not in {
                path.casefold() for path in allowed
            }:
                raise ValueError("Foreign executable/token in owned service ancestry")
            self.handles.append(handle)
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

        if self.job is not None:
            if win32job.QueryInformationJobObject(
                self.job, win32job.JobObjectBasicAccountingInformation
            )["ActiveProcesses"]:
                win32job.TerminateJobObject(self.job, 1)
            deadline = time.monotonic() + 15
            while win32job.QueryInformationJobObject(
                self.job, win32job.JobObjectBasicAccountingInformation
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
            for plan in (pg, app):
                self.scm.start(plan["name"])
                self.scm.wait(plan["name"], "running")
        return validate_resources(self.marker, self.scm)


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
        or pg.get("hostaddr") not in (None, "127.0.0.1")
        or any(key in pg for key in ("service", "options"))
    ):
        raise ValueError("Dedicated release API/database/storage scope required")


def preflight(root):
    marker = load_marker(root)
    import json

    from .lab_install import verify_tree

    config = json.loads((root / "config.json").read_text())
    validate_config(marker, config)
    from .lab_install import plain
    from .lab_scm import validate_pg_config

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

    def admit_start(self, root):
        from .restart_budget import BudgetDenied, BudgetUnavailable, DatabaseRestartBudget

        marker = load_marker(root)
        try:
            DatabaseRestartBudget.from_config(
                root / "config.json",
                root / "runtime",
                marker["installation_id"],
                marker["source_sha"],
            ).admit("host")
        except BudgetUnavailable:
            raise
        except BudgetDenied:
            return False
        return True

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
        return preflight(root)

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


if __name__ == "__main__":
    main()
