"""Native SCM host, usable only after separately approved pywin32/VM installation.

Import requires the windows-service optional dependency. Importing this module never
installs, repairs, starts or removes a service. SCM loads TesterService via PythonClass.
"""

import os
import subprocess
import threading
import time
from pathlib import Path

import servicemanager
import win32service
import win32serviceutil

from .config import load_settings
from .jobs import JobQueue


class TesterService(win32serviceutil.ServiceFramework):
    _svc_name_ = "TesterMarketLab"
    _svc_display_name_ = "Tester Market Laboratory"

    def __init__(self, args):
        super().__init__(args)
        self.stop = threading.Event()
        self.settings = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING, waitHint=30000)
        self.stop.set()
        if self.settings:
            try:
                JobQueue(self.settings.dsn).set_draining(True)
            finally:
                (self.settings.data_dir / "stop.request").touch()

    def SvcDoRun(self):
        config = win32serviceutil.GetServiceCustomOption(self._svc_name_, "ConfigPath")
        repo = Path(win32serviceutil.GetServiceCustomOption(self._svc_name_, "RepoPath"))
        os.environ["TESTER_CONFIG_FILE"] = config
        self.settings = load_settings()
        if self.stop.is_set():
            return
        python = repo / ".venv/Scripts/python.exe"
        child = subprocess.Popen(
            [str(python), "-m", "src.api.supervisor"],
            cwd=repo,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        servicemanager.LogInfoMsg("Tester native service started")
        try:
            while not self.stop.wait(0.25):
                if child.poll() is not None:
                    # Raise to SCM; bounded recovery policy is in the reviewed plan.
                    raise RuntimeError("Tester supervised process exited")
        finally:
            (self.settings.data_dir / "stop.request").touch()
            deadline = time.monotonic() + 25
            while child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
