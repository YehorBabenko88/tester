"""Actual private-venv dispatcher for the one owned physical acceptance service."""

import os
import secrets
import subprocess
import sys
import threading

from .physical_runtime import probe_health, supervise
from .physical_scm import NativeSCM, preflight
from .physical_scope import (
    ROOT,
    SERVICE,
    ScopeViolation,
    failure_budget,
    owned_service,
    plain_path,
    validate_owner,
    validate_release,
)


def dispatch():
    import servicemanager
    import win32service
    import win32serviceutil

    class PhysicalService(win32serviceutil.ServiceFramework):
        _svc_name_ = SERVICE
        _svc_display_name_ = SERVICE

        def __init__(self, args):
            if args[0] != SERVICE:
                raise ScopeViolation("Foreign SCM dispatcher name")
            super().__init__(args)
            self.stop_event = threading.Event()
            self.current_status = win32service.SERVICE_START_PENDING

        def report_status(self, status, **kwargs):
            self.current_status = status
            super().ReportServiceStatus(status, **kwargs)

        def SvcInterrogate(self):
            self.report_status(self.current_status)

        def SvcStop(self):
            self.report_status(win32service.SERVICE_STOP_PENDING, waitHint=10000)
            self.stop_event.set()

        def SvcShutdown(self):
            self.SvcStop()

        def SvcRun(self):
            # ServiceFramework.SvcRun would advertise RUNNING before readiness.
            before = None
            try:
                self.report_status(win32service.SERVICE_START_PENDING, waitHint=60000)
                validate_owner(ROOT)
                before = failure_budget(ROOT / "runtime")
                self.SvcDoRun()
            except Exception:
                try:
                    if before is not None and failure_budget(ROOT / "runtime") == before:
                        failure_budget(ROOT / "runtime", record="bootstrap_failed")
                except Exception:
                    pass  # exhausted/invalid ledger stays blocked, never reset
                servicemanager.LogErrorMsg(
                    "Physical Tester service failed; inspect scoped runtime logs/failure budget"
                )
                # Returning normally lets the C dispatcher overwrite STOPPED with success.
                # Propagate so native SCM recovery receives the failure.
                raise
            self.report_status(win32service.SERVICE_STOP_PENDING, waitHint=10000)

        def SvcDoRun(self):
            scm = NativeSCM()
            try:
                current = scm.inspect()
            finally:
                scm.close()
            if current is None:
                raise ScopeViolation("Service registration missing")
            owned_service(SERVICE, current["image"], current["options"])
            if current["account"].casefold() != "nt authority\\localservice":
                raise ScopeViolation("Unexpected service identity")
            release = validate_release(
                current["options"]["Release"], current["options"]["ManifestSHA256"]
            )
            if plain_path(__file__) != release / "src/api/physical_host.py":
                raise ScopeViolation("Wrong host release")
            if plain_path(sys.executable) != plain_path(ROOT / "venv/Scripts/python.exe"):
                raise ScopeViolation("Wrong private interpreter")
            # Prevent inherited TESTER_* or PYTHONPATH configuration injection.
            for key in list(os.environ):
                if key.startswith(("TESTER_", "PYTHON", "PG")):
                    os.environ.pop(key, None)
            os.environ["TESTER_CONFIG_FILE"] = str(ROOT / "config.json")
            os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
            os.environ["PYTHONNOUSERSITE"] = "1"
            instance = secrets.token_hex(16)
            os.environ["TESTER_SERVICE_INSTANCE"] = instance
            settings = preflight(release)

            def report(state):
                status = {
                    "starting": win32service.SERVICE_START_PENDING,
                    "running": win32service.SERVICE_RUNNING,
                    "stopping": win32service.SERVICE_STOP_PENDING,
                }[state]
                self.report_status(status, waitHint=10000 if state != "running" else 0)

            supervise(
                settings.data_dir,
                self.stop_event,
                lambda: JobProcess(release),
                lambda: probe_health(instance),
                report,
            )

    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(PhysicalService)
    servicemanager.StartServiceCtrlDispatcher()


class JobProcess:
    """Create suspended, put in a kill-on-close job, then resume; no orphan launch race."""

    def __init__(self, release):
        import win32api
        import win32event
        import win32job
        import win32process

        self.api, self.event, self.jobapi, self.processapi = (
            win32api,
            win32event,
            win32job,
            win32process,
        )
        # pywin32 requires a string for WCHAR*: None raises before child launch.
        self.job = win32job.CreateJobObject(None, "")
        self.process = None
        thread = None
        try:
            info = win32job.QueryInformationJobObject(
                self.job, win32job.JobObjectExtendedLimitInformation
            )
            info["BasicLimitInformation"]["LimitFlags"] |= (
                win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            win32job.SetInformationJobObject(
                self.job, win32job.JobObjectExtendedLimitInformation, info
            )
            command = subprocess.list2cmdline([sys.executable, "-B", "-m", "src.api.supervisor"])
            self.process, thread, _, _ = win32process.CreateProcess(
                sys.executable,
                command,
                None,
                None,
                False,
                0x4 | 0x08000000,
                dict(os.environ),
                str(release),
                win32process.STARTUPINFO(),
            )
            win32job.AssignProcessToJobObject(self.job, self.process)
            win32process.ResumeThread(thread)
        except Exception:
            if self.process is not None:
                win32process.TerminateProcess(self.process, 1)
            self.close()
            raise
        finally:
            if thread is not None:
                win32api.CloseHandle(thread)

    def poll(self):
        if self.event.WaitForSingleObject(self.process, 0) == 0:
            return self.processapi.GetExitCodeProcess(self.process)
        return None

    def kill_tree(self):
        self.jobapi.TerminateJobObject(self.job, 1)

    def close(self):
        if self.job is not None:
            self.api.CloseHandle(self.job)
            self.job = None
        if self.process is not None:
            self.api.CloseHandle(self.process)
            self.process = None
