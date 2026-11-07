"""SCM change-plan generation ONLY. No service/registry/ACL mutation capability."""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def service_plan(action: str, repo: Path) -> dict:
    if action not in {"install", "repair", "uninstall"}:
        raise ValueError("Unsupported service plan action")
    repo = repo.resolve()
    service = "TesterMarketLab"
    host = repo / ".venv/Scripts/python.exe"
    dispatcher = repo / "scripts/windows/service_host.py"
    operations = []
    if action == "install":
        operations.append(
            {
                "api": "win32service.CreateService",
                "serviceName": service,
                "displayName": "Tester Market Laboratory",
                "binaryPathName": subprocess.list2cmdline([str(host), str(dispatcher)]),
                "startType": "SERVICE_AUTO_START",
                "userName": "NT SERVICE\\" + service,
            }
        )
    if action in {"install", "repair"}:
        operations.extend(
            [
                {
                    "api": "ChangeServiceConfig2",
                    "setting": "SERVICE_CONFIG_DELAYED_AUTO_START_INFO",
                    "value": True,
                },
                {
                    "api": "ChangeServiceConfig2",
                    "setting": "SERVICE_CONFIG_SERVICE_SID_INFO",
                    "value": "SERVICE_SID_TYPE_UNRESTRICTED",
                },
                {
                    "api": "ChangeServiceConfig2",
                    "setting": "SERVICE_CONFIG_FAILURE_ACTIONS",
                    "reset_seconds": 86400,
                    "actions": [
                        {"action": "restart", "delay_ms": 5000},
                        {"action": "restart", "delay_ms": 15000},
                        {"action": "restart", "delay_ms": 60000},
                        {"action": "none", "delay_ms": 0},
                    ],
                },
                {
                    "api": "SetServiceCustomOption",
                    "ConfigPath": str(repo / "runtime/config.json"),
                    "RepoPath": str(repo),
                },
                {
                    "api": "reviewed_acl_change",
                    "identity": "NT SERVICE\\" + service,
                    "read_execute": [str(repo), str(repo / ".venv"), sys.base_prefix],
                    "modify": [str(repo / "runtime")],
                    "preserve_user_and_SYSTEM": True,
                },
            ]
        )
    if action == "uninstall":
        operations.extend(
            [
                {
                    "api": "ControlService",
                    "value": "SERVICE_CONTROL_STOP",
                    "drain_deadline_seconds": 30,
                },
                {"api": "DeleteService", "serviceName": service},
                {
                    "api": "reviewed_acl_change",
                    "remove_only_service_identity": True,
                    "retain_database_role_and_runtime": True,
                },
            ]
        )
    return {
        "schema_version": "1.0",
        "mode": "DRY_RUN",
        "action": action,
        "physical_changes": False,
        "service_name": service,
        "identity": "NT SERVICE\\" + service,
        "host_kind": "native_pywin32_ServiceFramework",
        "delayed_auto_start": True,
        "restart_policy": {"max_restarts": 3, "reset_seconds": 86400},
        "operations": operations,
        "unverified_gates": [
            "disposable_VM_SCM_start_stop",
            "VM_reboot",
            "native_host_DLL_resolution",
            "virtual_account_ACL_access",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "repair", "uninstall"])
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(service_plan(args.action, args.repo), indent=2))


if __name__ == "__main__":
    main()
