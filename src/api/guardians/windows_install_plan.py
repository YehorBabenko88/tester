"""Pure ADMINIT TESTER layout and desktop shortcut plan (no host effects)."""

from __future__ import annotations

import ntpath
from dataclasses import dataclass

from src.api.guardians.windows_install_gate import Gate, canonical, validate_manifest

ROOT = "c:\\testerlab"
DIRECTORIES = (
    "app",
    "config",
    "data",
    "postgres-data",
    "logs",
    "scripts",
    "install",
)
START_TITLE = "TESTER — Запуск"
REMOVE_TITLE = "TESTER — Удаление"


@dataclass(frozen=True)
class ShortcutSpec:
    title: str
    target: str
    arguments: str
    working_directory: str


@dataclass(frozen=True)
class InstallPlan:
    allowed: bool
    reason: str
    directories: tuple[str, ...] = ()
    shortcuts: tuple[ShortcutSpec, ...] = ()


def plan_install(
    manifest: dict, attestation: dict, *, signed_bundle_verified: bool = False
) -> InstallPlan:
    """Return a pinned, non-effectful plan; not permission to deploy."""
    gate: Gate = validate_manifest(manifest, attested=attestation)
    if not gate.allowed:
        return InstallPlan(False, gate.reason)
    root = canonical(manifest.get("root"))
    if root != ROOT:
        return InstallPlan(False, "wrong_adminit_root")
    if signed_bundle_verified is not True:
        return InstallPlan(False, "bundle_signature_unverified")
    scripts = ntpath.join(root, "scripts")
    launcher = ntpath.join(scripts, "Start-Tester.ps1")
    uninstaller = ntpath.join(scripts, "Uninstall-Tester.ps1")
    specs = (
        ShortcutSpec(
            START_TITLE,
            "powershell.exe",
            '-NoProfile -ExecutionPolicy AllSigned -File "' + launcher + '"',
            root,
        ),
        ShortcutSpec(
            REMOVE_TITLE,
            "powershell.exe",
            '-NoProfile -ExecutionPolicy AllSigned -File "' + uninstaller + '"',
            root,
        ),
    )
    return InstallPlan(
        True,
        "plan_only_no_deployment",
        tuple(ntpath.join(root, d) for d in DIRECTORIES),
        specs,
    )
