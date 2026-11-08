"""Upgrade proposal helpers run in scratch only; no real SCM mutations."""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="PowerShell proposal helpers")


def test_upgrade_inventory_refuses_extra_files_and_traversal(tmp_path):
    library = Path("scripts/windows/Update-TesterLabRelease.ps1").absolute()
    assert library.is_file(), "published upgrade proposal missing"
    root = tmp_path / "tree"
    root.mkdir()
    (root / "good.txt").write_text("published")
    script = tmp_path / "verify.ps1"
    script.write_text(
        r"""
param($Library,$Tree)
$ErrorActionPreference='Stop'
. $Library -FunctionsOnly
$Hash=(Get-FileHash -LiteralPath (Join-Path $Tree 'good.txt') -Algorithm SHA256).Hash.ToLowerInvariant()
$Files=[pscustomobject]@{'good.txt'=$Hash}
Assert-UpgradeTree $Tree $Files
Set-Content -LiteralPath (Join-Path $Tree 'extra.txt') 'foreign'
$Refused=$false
try {Assert-UpgradeTree $Tree $Files} catch {$Refused=$true}
if (-not $Refused) {throw 'Extra file adopted'}
$Refused=$false
try {Assert-UpgradeTree $Tree ([pscustomobject]@{'../escape'=$Hash})} catch {$Refused=$true}
if (-not $Refused) {throw 'Traversal accepted'}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-File", str(script), str(library), str(root)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_upgrade_native_changes_are_bounded(tmp_path):
    library = Path("scripts/windows/Update-TesterLabRelease.ps1").absolute()
    assert library.is_file(), "published upgrade proposal missing"
    script = tmp_path / "diff.ps1"
    script.write_text(
        r"""
param($Library)
$ErrorActionPreference='Stop'
. $Library -FunctionsOnly
$Old=[pscustomobject]@{'pythonservice.exe'='same';'Lib/site-packages/tester-lab.pth'='old'}
$Good=[pscustomobject]@{'pythonservice.exe'='same';'Lib/site-packages/tester-lab.pth'='new'}
Assert-UpgradeNativeDiff $Old $Good
$Refused=$false
try {Assert-UpgradeNativeDiff $Old ([pscustomobject]@{'pythonservice.exe'='changed';'Lib/site-packages/tester-lab.pth'='new'})} catch {$Refused=$true}
if (-not $Refused) {throw 'Unrelated host binary mutation accepted'}
$Refused=$false
try {Assert-UpgradeNativeDiff $Old ([pscustomobject]@{'Lib/site-packages/tester-lab.pth'='new'})} catch {$Refused=$true}
if (-not $Refused) {throw 'Host binary removal accepted'}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-File", str(script), str(library)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_upgrade_action_targets_app_only_with_mocked_services(tmp_path):
    library = Path("scripts/windows/Update-TesterLabRelease.ps1").absolute()
    script = tmp_path / "app-only.ps1"
    script.write_text(
        r"""
param($Library)
$ErrorActionPreference='Stop'
. $Library -FunctionsOnly
$script:Owner=[pscustomobject]@{services=[pscustomobject]@{app='owned-app';postgres='owned-pg'}}
$script:Calls=[Collections.Generic.List[string]]::new()
function Assert-UpgradeOwnedServices {param([string[]]$AllowedSHA) $script:Calls.Add('verified');return [pscustomobject]@{}}
function Get-Service {param([string]$Name) if ($Name -ne 'owned-app') {throw 'PG selected'}; $Service=[pscustomobject]@{Name=$Name;Status='Running'};$Service|Add-Member ScriptMethod WaitForStatus {param($State,$Timeout)};return $Service}
function Stop-Service {param($InputObject) $script:Calls.Add('stop:'+$InputObject.Name)}
function Start-Service {param($InputObject) $script:Calls.Add('start:'+$InputObject.Name)}
Invoke-UpgradeAppAction stop @('old')
Invoke-UpgradeAppAction start @('new')
if (($script:Calls -join ',') -ne 'verified,stop:owned-app,verified,start:owned-app') {throw 'Incorrect app-only transition order'}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-File", str(script), str(library)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_upgrade_registry_changes_preserve_named_paths_and_values(tmp_path):
    library = Path("scripts/windows/Update-TesterLabRelease.ps1").absolute()
    script = tmp_path / "registry.ps1"
    script.write_text(
        r"""
param($Library)
$ErrorActionPreference='Stop'
. $Library -FunctionsOnly
$Changes=@(Get-UpgradeRegistryChanges 'SYSTEM\Owned' 'newsha' 'native.Class' 'native;source;site')
if ($Changes.Count -ne 3 -or $Changes[0].path -cne 'SYSTEM\Owned\Parameters' -or $Changes[0].name -cne 'SourceSHA' -or $Changes[0].value -cne 'newsha') {throw 'Bad SourceSHA registry update'}
if ($Changes[1].path -cne 'SYSTEM\Owned\PythonClass' -or $Changes[1].name -cne '' -or $Changes[1].value -cne 'native.Class') {throw 'Bad class registry update'}
if ($Changes[2].path -cne 'SYSTEM\Owned\PythonPath' -or $Changes[2].value -cne 'native;source;site') {throw 'Bad PythonPath registry update'}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-File", str(script), str(library)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_upgrade_backup_guard_refuses_junction_before_bundle_creation(tmp_path):
    target = tmp_path / "foreign"
    target.mkdir()
    link = tmp_path / "backups"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True
    )
    library = Path("scripts/windows/Update-TesterLabRelease.ps1").absolute()
    script = tmp_path / "backup-guard.ps1"
    script.write_text(
        r"""
param($Library,$Backup)
$ErrorActionPreference='Stop'
. $Library -FunctionsOnly
$Refused=$false
try {Assert-UpgradeBackupDestination $Backup (Join-Path $Backup ('upgrade-'+('a'*32)))} catch {if ($_.Exception.Message -notmatch 'Reparse') {throw};$Refused=$true}
if (-not $Refused) {throw 'Foreign backup junction accepted'}
""",
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-File", str(script), str(library), str(link)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert list(target.iterdir()) == []
    finally:
        link.rmdir()
