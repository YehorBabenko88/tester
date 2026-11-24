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

    service_role = "app"
    uses_http_health = True
    marker_reader = staticmethod(check)
    contract_builder = staticmethod(launch_contract)

    def service_preflight(self, root):
        from .lab_scm import preflight

        return preflight(root)

    def evidence_path(self, root):
        return plain(root / "runtime/service.json")

    def signal_stop(self, root):
        plain(root / "runtime/stop.request").touch()

    def graceful_stop(self, root):
        self.signal_stop(root)

    def ready_without_http(self, root, marker, instance):
        return False

    def accepts_child_exit(self, exit_code):
        return False

    def admit_start(self, root):
        return True

    def create_job(self, marker):
        import win32job

        return win32job.CreateJobObject(None, "")

    def record_phase(self, phase):
        """Optional release lifecycle receipt; legacy behavior stays unchanged."""

    def health_port(self, marker):
        return 18766

    def health_ready(self, response, health, instance):
        return (
            response.status == 200
            and health.get("status") == "ok"
            and health.get("instance_id") == instance
        )

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
            self.signal_stop(self.root)
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

        key = "SYSTEM\\CurrentControlSet\\Services\\" + self.service_name + "\\Parameters"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_QUERY_VALUE) as reg:
            root = plain(winreg.QueryValueEx(reg, "Root")[0])
            ident = winreg.QueryValueEx(reg, "InstallationId")[0]
            sha = winreg.QueryValueEx(reg, "SourceSHA")[0]
        marker = self.marker_reader(root)
        if (
            marker["installation_id"] != ident
            or marker["services"][self.service_role] != self.service_name
            or marker.get("active_sha", marker.get("source_sha")) != sha
        ):
            raise LabViolation("Registered release/ownership does not match marker")
        self.service_preflight(root)
        self.root = root
        instance = instance_token()
        self.instance = instance
        if not self.admit_start(root):
            self.record_phase("restart-budget-denied")
            return  # Normal STOPPED: budget denial must not trigger SCM recovery.
        contract = self.contract_builder(root)
        self.record_phase("starting")
        contract["env"]["TESTER_SERVICE_INSTANCE"] = instance
        job = self.create_job(marker)
        limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
        process = thread = None
        assigned = False
        evidence = self.evidence_path(root)
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
            self.child_pid = pid
            win32job.AssignProcessToJobObject(job, process)
            assigned = True
            if win32event.WaitForSingleObject(self.stop_event, 0) == win32event.WAIT_OBJECT_0:
                self.record_phase("startup-interrupted")
                return  # Suspended child is destroyed when the job closes.
            win32process.ResumeThread(thread)
            deadline = time.monotonic() + 55
            while True:
                if win32event.WaitForSingleObject(self.stop_event, 0) == win32event.WAIT_OBJECT_0:
                    # Startup cancellation kills the job rather than racing supervisor's initial unlink.
                    self.record_phase("startup-interrupted")
                    return
                if win32event.WaitForSingleObject(process, 0) == win32event.WAIT_OBJECT_0:
                    if self.accepts_child_exit(win32process.GetExitCodeProcess(process)):
                        return
                    raise LabViolation("Unexpected supervisor exit during startup")
                if self.ready_without_http(root, marker, instance):
                    break
                if self.uses_http_health:
                    try:
                        connection = HTTPConnection(
                            "127.0.0.1", self.health_port(marker), timeout=1
                        )
                        try:
                            connection.request("GET", "/api/health")
                            response = connection.getresponse()
                            health = json.loads(response.read())["data"]
                        finally:
                            connection.close()
                        if self.health_ready(response, health, instance):
                            break
                    except (OSError, ValueError, KeyError):
                        pass
                if time.monotonic() > deadline:
                    raise LabViolation("Service health readiness timed out")
                self.ReportServiceStatus(win32service.SERVICE_START_PENDING, waitHint=60000)
                win32event.WaitForSingleObject(self.stop_event, 250)
            self.record_phase("running")
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
                self.record_phase("stopping")
                self.graceful_stop(root)
                if win32event.WaitForSingleObject(process, 32000) != win32event.WAIT_OBJECT_0:
                    raise LabViolation("Graceful shutdown timed out")
                if win32process.GetExitCodeProcess(process) != 0:
                    raise LabViolation("Supervisor shutdown failed")
            elif not self.accepts_child_exit(win32process.GetExitCodeProcess(process)):
                raise LabViolation("Unexpected supervisor exit")
        finally:
            if process is not None and not assigned:
                win32api.TerminateProcess(process, 1)
            win32api.CloseHandle(job)
            if thread is not None:
                win32api.CloseHandle(thread)
            if process is not None:
                win32api.CloseHandle(process)
            self.record_phase("stopped")
            evidence.unlink(missing_ok=True)
