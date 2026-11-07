"""Scoped physical SCM commands. Importing this module performs no native actions."""

import argparse
import json
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict

from .config import Settings
from .physical_scope import (
    OWNER,
    PG_SERVICE,
    ROOT,
    SERVICE,
    ScopeViolation,
    failure_budget,
    image_path,
    owned_service,
    plain_path,
    release_path,
    validate_owner,
    validate_release,
)


class PhysicalSettings(Settings):
    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (init_settings,)


def preflight(release, root=ROOT):
    root = plain_path(root)
    python = plain_path(root / "venv/Scripts/python.exe")
    config = plain_path(root / "config.json")
    runtime = plain_path(root / "runtime")
    if not python.is_file() or not runtime.is_dir():
        raise ScopeViolation("MASTER must prepare private venv and runtime")
    data = json.loads(config.read_text(encoding="utf-8-sig"))
    if not {"database_url", "host", "port", "data_dir", "frontend_dir"} <= data.keys():
        raise ScopeViolation("Physical config requires explicit endpoint/path/database fields")
    settings = PhysicalSettings(**data)
    if settings.host != "127.0.0.1" or settings.port != 18765 or settings.data_dir != runtime:
        raise ScopeViolation("Config differs from physical acceptance scope")
    if not settings.dsn:
        raise ScopeViolation("Dedicated PostgreSQL55469 DSN required")
    pg = conninfo_to_dict(settings.dsn)
    if (
        pg.get("host") != "127.0.0.1"
        or pg.get("port") != "55469"
        or pg.get("service")
        or pg.get("hostaddr")
    ):
        raise ScopeViolation("Only explicit local PostgreSQL55469 is allowed")
    if plain_path(settings.frontend_dir) != release / "frontend":
        raise ScopeViolation("Frontend must belong to selected release")
    failure_budget(runtime)
    return settings


def apply(action, release, digest, scm, *, root=ROOT, dependency=False, preflight=preflight):
    root = plain_path(root)
    release = release_path(release, root)
    owner = validate_owner(root)
    current = scm.inspect()
    if current is not None:
        owned_service(SERVICE, current["image"], current["options"], root=root)
        if current["account"].casefold() != "nt authority\\localservice":
            raise ScopeViolation("Existing service account differs from least-privilege contract")
    if action == "install":
        if current is not None:
            raise ScopeViolation("Service already exists; explicit stopped update required")
    elif current is None:
        raise ScopeViolation("Owned service is not installed")
    if action in {"install", "update", "start"}:
        validate_release(release, digest, root=root)
        preflight(release, root)
    if action in {"start", "stop", "status"}:
        if (
            current["options"]["Release"] != str(release)
            or current["options"]["ManifestSHA256"] != digest
        ):
            raise ScopeViolation("Command must name currently registered release/digest")
    options = {
        "Owner": OWNER,
        "InstallationId": owner["installation_id"],
        "Root": str(root),
        "Release": str(release),
        "ManifestSHA256": digest,
        "ConfigPath": str(root / "config.json"),
    }
    if action in {"install", "update"}:
        if current is not None and current["state"] != "stopped":
            raise ScopeViolation("Stop owned service before changing its release")
        getattr(scm, action)(image_path(release, root), options, PG_SERVICE if dependency else None)
    elif action == "start":
        if current["state"] != "stopped":
            raise ScopeViolation("Service must be stopped before start")
        scm.start()
    elif action == "stop":
        if current["state"] != "stopped":
            scm.stop()
    elif action != "status":
        raise ScopeViolation("Unsupported action")
    return {
        "service": SERVICE,
        "action": action,
        "release": str(release),
        "state_before": None if current is None else current["state"],
    }


def read_options():
    # pywin32 GetServiceCustomOption uses RegCreateKey (write access), unsuitable for LocalService.
    import winreg

    path = "SYSTEM\\CurrentControlSet\\Services\\" + SERVICE + "\\Parameters"
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_QUERY_VALUE) as key:
        return {
            name: winreg.QueryValueEx(key, name)[0]
            for name in (
                "Owner",
                "Root",
                "Release",
                "ManifestSHA256",
                "ConfigPath",
                "InstallationId",
            )
        }


class NativeSCM:
    def __init__(self, installing=False):
        import win32service as ws
        import win32serviceutil as util

        self.ws, self.util = ws, util
        self.manager = ws.OpenSCManager(
            None, None, ws.SC_MANAGER_CONNECT | (ws.SC_MANAGER_CREATE_SERVICE if installing else 0)
        )

    def close(self):
        self.ws.CloseServiceHandle(self.manager)

    def inspect(self):
        ws = self.ws
        try:
            handle = ws.OpenService(
                self.manager, SERVICE, ws.SERVICE_QUERY_CONFIG | ws.SERVICE_QUERY_STATUS
            )
        except Exception as exc:
            if getattr(exc, "winerror", None) == 1060:
                return None
            raise
        try:
            conf = ws.QueryServiceConfig(handle)
            state = ws.QueryServiceStatus(handle)[1]
        finally:
            ws.CloseServiceHandle(handle)
        options = read_options()
        return dict(
            image=conf[3],
            account=conf[7],
            state="stopped" if state == ws.SERVICE_STOPPED else "running",
            options=options,
        )

    def _configure(self, handle, options):
        ws = self.ws
        # Two delayed recoveries, then no action. Persisted runtime budget also survives manual starts.
        ws.ChangeServiceConfig2(
            handle,
            ws.SERVICE_CONFIG_FAILURE_ACTIONS,
            {
                "ResetPeriod": 0xFFFFFFFF,
                "RebootMsg": "",
                "Command": "",
                "Actions": [
                    (ws.SC_ACTION_RESTART, 15000),
                    (ws.SC_ACTION_RESTART, 30000),
                    (ws.SC_ACTION_NONE, 0),
                ],
            },
        )
        ws.ChangeServiceConfig2(handle, ws.SERVICE_CONFIG_FAILURE_ACTIONS_FLAG, True)
        ws.ChangeServiceConfig2(
            handle, ws.SERVICE_CONFIG_DESCRIPTION, "Scoped physical Tester acceptance; no trading"
        )
        for key, value in options.items():
            self.util.SetServiceCustomOption(SERVICE, key, value)

    def install(self, image, options, dependency):
        ws = self.ws
        handle = ws.CreateService(
            self.manager,
            SERVICE,
            SERVICE,
            ws.SERVICE_CHANGE_CONFIG | ws.SERVICE_START,
            ws.SERVICE_WIN32_OWN_PROCESS,
            ws.SERVICE_DEMAND_START,
            ws.SERVICE_ERROR_NORMAL,
            image,
            None,
            0,
            [dependency] if dependency else None,
            "NT AUTHORITY\\LocalService",
            None,
        )
        try:
            self._configure(handle, options)
        finally:
            ws.CloseServiceHandle(handle)

    def update(self, image, options, dependency):
        ws = self.ws
        handle = ws.OpenService(self.manager, SERVICE, ws.SERVICE_CHANGE_CONFIG | ws.SERVICE_START)
        try:
            ws.ChangeServiceConfig(
                handle,
                ws.SERVICE_NO_CHANGE,
                ws.SERVICE_DEMAND_START,
                ws.SERVICE_ERROR_NORMAL,
                image,
                None,
                0,
                [dependency] if dependency else [],
                "NT AUTHORITY\\LocalService",
                None,
                SERVICE,
            )
            self._configure(handle, options)
        finally:
            ws.CloseServiceHandle(handle)

    def start(self):
        ws = self.ws
        handle = ws.OpenService(self.manager, SERVICE, ws.SERVICE_START)
        try:
            ws.StartService(handle, None)
        finally:
            ws.CloseServiceHandle(handle)

    def stop(self):
        ws = self.ws
        handle = ws.OpenService(self.manager, SERVICE, ws.SERVICE_STOP)
        try:
            ws.ControlService(handle, ws.SERVICE_CONTROL_STOP)
        finally:
            ws.CloseServiceHandle(handle)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["install", "update", "start", "stop", "status"])
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--with-postgres-service", action="store_true")
    args = parser.parse_args()
    # The entry script itself must be inside the approved immutable release.
    validate_release(args.release, args.manifest_sha256)
    if Path(__file__).resolve() != args.release.resolve() / "src/api/physical_scm.py":
        raise ScopeViolation("Installer must execute from the approved release")
    scm = NativeSCM(installing=args.action == "install")
    try:
        print(
            json.dumps(
                apply(
                    args.action,
                    args.release,
                    args.manifest_sha256,
                    scm,
                    dependency=args.with_postgres_service,
                )
            )
        )
    finally:
        scm.close()
