"""Regression tests use only scratch ACLs, never the physical lab or SCM."""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows filesystem ACL tests")


def test_explicit_acl_grants_apply_to_files_and_directories(tmp_path):
    library = Path("scripts/windows/Repair-TesterLabPortableAcl.ps1").absolute()
    assert library.is_file(), "explicit ACL recovery library missing"
    script = tmp_path / "verify.ps1"
    script.write_text(
        r"""
param($Library,$Scratch)
$ErrorActionPreference='Stop'
. $Library -FunctionsOnly
$Me=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$Directory=Join-Path $Scratch 'tree'
New-Item -ItemType Directory -Path $Directory | Out-Null
$File=Join-Path $Directory 'leaf.txt'
Set-Content -LiteralPath $File 'preserve'
Set-TesterLabExplicitAcl -ItemPath $Directory -CreatorSid $Me -CreatorRights ReadAndExecute -ServiceRights ReadAndExecute
Set-TesterLabExplicitAcl -ItemPath $File -CreatorSid $Me -CreatorRights ReadAndExecute -ServiceRights ReadAndExecute
foreach ($Path in @($Directory,$File)) {
  $Acl=Get-Acl -LiteralPath $Path
  $Creator=@($Acl.Access | Where-Object {$_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -eq $Me})
  if ($Creator.Count -ne 1 -or $Creator[0].IsInherited -or $Creator[0].FileSystemRights -band [Security.AccessControl.FileSystemRights]::ReadAndExecute -ne [Security.AccessControl.FileSystemRights]::ReadAndExecute) { throw 'Creator effective ACE missing' }
  if (-not $Acl.AreAccessRulesProtected) { throw 'DACL must be explicit/protected' }
}
if ((Get-Content -LiteralPath $File -Raw).Trim() -ne 'preserve') {throw 'Bytes changed'}
""",
        encoding="utf-8",
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-File", str(script), str(library), str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )


def test_owner_recovers_empty_file_dacl_without_write_owner(tmp_path):
    library = Path("scripts/windows/Repair-TesterLabPortableAcl.ps1").absolute()
    script = tmp_path / "empty.ps1"
    script.write_text(
        r"""
param($Library,$Scratch)
$ErrorActionPreference='Stop'
. $Library -FunctionsOnly
$Me=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$File=Join-Path $Scratch 'empty.txt'
Set-Content -LiteralPath $File 'unchanged'
$OwnerBefore=Get-TesterLabOwnerSid $File
$Empty=[Security.AccessControl.FileSecurity]::new()
$Empty.SetAccessRuleProtection($true,$false)
Set-Acl -LiteralPath $File -AclObject $Empty
if (@((Get-Acl -LiteralPath $File).Access).Count -ne 0) {throw 'Fixture is not empty DACL'}
Set-TesterLabExplicitAcl -ItemPath $File -CreatorSid $Me -CreatorRights ReadAndExecute
if ((Get-TesterLabOwnerSid $File) -ne $OwnerBefore) {throw 'Owner changed'}
if ((Get-Content -LiteralPath $File -Raw).Trim() -ne 'unchanged') {throw 'Bytes changed'}
$Principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
Write-Output ('IsAdmin='+$Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-File", str(script), str(library), str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "IsAdmin=False" in result.stdout


def test_repair_plan_skips_unreadable_and_foreign_owners(tmp_path):
    import json
    import uuid

    root = tmp_path / "lab"
    root.mkdir()
    ident = uuid.uuid4()
    (root / ".tester-lab.json").write_text(
        json.dumps(
            dict(
                schema="tester-lab/v1",
                root=str(root),
                repository="YehorBabenko88/tester",
                installation_id=str(ident),
                services=dict(app="TesterLabApp_" + ident.hex, postgres="TesterLabPG_" + ident.hex),
            )
        )
    )
    for name in ["unreadable.log", "foreign.log", "config.json"]:
        (root / name).write_text("keep")
    (root / "releases").mkdir()
    (root / "private").mkdir()
    (root / "private/db.json").write_text("private")
    script = tmp_path / "plan.ps1"
    script.write_text(
        r"""
param($Library,$Root,$Id)
$ErrorActionPreference='Stop'
. $Library -FunctionsOnly
function Get-Acl {
  param([string]$LiteralPath)
  if ($LiteralPath.EndsWith('unreadable.log')) {throw [UnauthorizedAccessException]::new('fixture denied')}
  $Acl=Microsoft.PowerShell.Security\Get-Acl -LiteralPath $LiteralPath
  if ($LiteralPath.EndsWith('foreign.log')) {$Acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-18'))}
  return $Acl
}
Get-TesterLabAclRepairPlan -Root $Root -InstallationId $Id | ConvertTo-Json -Depth 5
""",
        encoding="utf-8",
    )
    library = Path("scripts/windows/Repair-TesterLabPortableAcl.ps1").absolute()
    result = subprocess.run(
        ["powershell", "-NoProfile", "-File", str(script), str(library), str(root), str(ident)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    plan = {
        Path(row["path"]).relative_to(root).as_posix(): row for row in json.loads(result.stdout)
    }
    assert plan["unreadable.log"]["skip"] == "acl-unreadable"
    assert plan["unreadable.log"]["original_sddl"] is None
    assert plan["foreign.log"]["skip"] == "foreign-owner"
    assert plan["."]["creator_rights"] == "Modify"
    assert plan["releases"]["creator_rights"] == "Modify"
    assert plan["config.json"]["creator_rights"] == "Modify"
    assert plan["private/db.json"]["creator_rights"] == "Read"
    assert (root / "private/db.json").read_text() == "private"


def test_recovery_plan_refuses_junction_before_any_mutation(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "junction"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True
    )
    script = tmp_path / "junction.ps1"
    script.write_text(
        r"""
param($Library,$Root)
$ErrorActionPreference='Stop'
. $Library -FunctionsOnly
try {Get-TesterLabAclRepairPlan -Root $Root -InstallationId '89189096-be4c-4cb3-bf2b-07b44f25fd60'; throw 'Unexpected success'}
catch {if ($_.Exception.Message -notlike '*Reparse*') {throw}}
""",
        encoding="utf-8",
    )
    library = Path("scripts/windows/Repair-TesterLabPortableAcl.ps1").absolute()
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-File", str(script), str(library), str(link)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert not list(target.iterdir())
    finally:
        link.rmdir()
