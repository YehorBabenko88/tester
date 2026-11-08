"""pywin32 pythonservice.exe hosted ServiceFramework; bounded descendant lifetime."""

import json
import os
import subprocess
import sys
import time
import uuid
from http.client import HTTPConnection

from .lab_install import LabViolation, check, plain

sys.dont_write_bytecode = True


def instance_token():
    return uuid.uuid4().hex


try:
    import win32serviceutil

    _Framework = win32serviceutil.ServiceFramework
except ImportError:
    _Framework = object  # Allows portable contract verification; cannot run a service.


def launch_contract(root):
    root = plain(root)
    marker = check(root)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("TESTER_", "PYTHON"))}
    env["TESTER_CONFIG_FILE"] = str(root / "config.json")
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return dict(
        argv=[str(root / "dependencies/venv/Scripts/python.exe"), "-m", "src.api.supervisor"],
        cwd=str(root / "releases" / marker["active_sha"]),
        env=env,
        sha=marker["active_sha"],
    )


class LabService(_Framework):
    _svc_name_ = "TesterLabDynamic"
    _svc_display_name_ = "Tester fresh isolated lab"

    def __init__(self, args):
        import win32event

        if _Framework is object:
            raise RuntimeError("Pinned pywin32 service runtime is required")
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, True, False, None)
        self.service_name = args[0]
        self.root = None

    def SvcStop(self):
        import win32event
        import win32service

        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING, waitHint=35000)
        if self.root is not None:
            plain(self.root / "runtime/stop.request").touch()
        win32event.SetEvent(self.stop_event)

    def SvcRun(self):
        import win32service

        self.ReportServiceStatus(win32service.SERVICE_START_PENDING, waitHint=60000)
        try:
            self.SvcDoRun()
        except Exception:
            self.ReportServiceStatus(
                win32service.SERVICE_STOPPED, win32ExitCode=1066, svcExitCode=1
            )
            raise
        self.ReportServiceStatus(win32service.SERVICE_STOPPED)

    def SvcDoRun(self):
        import winreg

        import win32api
        import win32event
        import win32job
        import win32process
        import win32service

        from .lab_scm import preflight

        key = "SYSTEM\\CurrentControlSet\\Services\\" + self.service_name + "\\Parameters"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_QUERY_VALUE) as reg:
            root = plain(winreg.QueryValueEx(reg, "Root")[0])
            ident = winreg.QueryValueEx(reg, "InstallationId")[0]
            sha = winreg.QueryValueEx(reg, "SourceSHA")[0]
        marker = check(root)
        if (
            marker["installation_id"] != ident
            or marker["services"]["app"] != self.service_name
            or marker["active_sha"] != sha
        ):
            raise LabViolation("Registered release/ownership does not match marker")
        preflight(root)
        self.root = root
        contract = launch_contract(root)
        instance = instance_token()
        contract["env"]["TESTER_SERVICE_INSTANCE"] = instance
        job = win32job.CreateJobObject(None, "")
        limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
        process = thread = None
        assigned = False
        evidence = plain(root / "runtime/service.json")
        try:
            # Suspend before assignment: descendants cannot escape before entering the job.
            process, thread, pid, _ = win32process.CreateProcess(
                contract["argv"][0],
                subprocess.list2cmdline(contract["argv"]),
                None,
                None,
                False,
                win32process.CREATE_SUSPENDED | win32process.CREATE_NO_WINDOW,
                contract["env"],
                contract["cwd"],
                win32process.STARTUPINFO(),
            )
            win32job.AssignProcessToJobObject(job, process)
            assigned = True
            if win32event.WaitForSingleObject(self.stop_event, 0) == win32event.WAIT_OBJECT_0:
                return  # Suspended child is destroyed when the job closes.
            win32process.ResumeThread(thread)
            deadline = time.monotonic() + 55
            while True:
                if win32event.WaitForSingleObject(self.stop_event, 0) == win32event.WAIT_OBJECT_0:
                    # Startup cancellation kills the job rather than racing supervisor's initial unlink.
                    return
                if win32event.WaitForSingleObject(process, 0) == win32event.WAIT_OBJECT_0:
                    raise LabViolation("Unexpected supervisor exit during startup")
                try:
                    connection = HTTPConnection("127.0.0.1", 18766, timeout=1)
                    try:
                        connection.request("GET", "/api/health")
                        response = connection.getresponse()
                        health = json.loads(response.read())["data"]
                    finally:
                        connection.close()
                    if (
                        response.status == 200
                        and health.get("status") == "ok"
                        and health.get("instance_id") == instance
                    ):
                        break
                except (OSError, ValueError, KeyError):
                    pass
                if time.monotonic() > deadline:
                    raise LabViolation("Service health readiness timed out")
                self.ReportServiceStatus(win32service.SERVICE_START_PENDING, waitHint=60000)
                win32event.WaitForSingleObject(self.stop_event, 250)
            self.ReportServiceStatus(win32service.SERVICE_RUNNING)
            evidence.write_text(
                json.dumps(
                    dict(
                        source_sha=sha,
                        installation_id=ident,
                        service=self.service_name,
                        supervisor_pid=pid,
                        instance_id=instance,
                        started_at=time.time(),
                    )
                ),
                encoding="utf-8",
            )
            result = win32event.WaitForMultipleObjects(
                [self.stop_event, process], False, win32event.INFINITE
            )
            if result == win32event.WAIT_OBJECT_0:
                # supervisor drains admission/claims then waits children; job kills survivors at deadline.
                plain(root / "runtime/stop.request").touch()
                if win32event.WaitForSingleObject(process, 32000) != win32event.WAIT_OBJECT_0:
                    raise LabViolation("Graceful shutdown timed out")
                if win32process.GetExitCodeProcess(process) != 0:
                    raise LabViolation("Supervisor shutdown failed")
            else:
                raise LabViolation("Unexpected supervisor exit")
        finally:
            if process is not None and not assigned:
                win32api.TerminateProcess(process, 1)
            win32api.CloseHandle(job)
            if thread is not None:
                win32api.CloseHandle(thread)
            if process is not None:
                win32api.CloseHandle(process)
            evidence.unlink(missing_ok=True)
