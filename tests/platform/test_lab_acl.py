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


def test_owner_walk_recovers_locked_nested_directories_with_journal(tmp_path):
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
    nested = root / "private/locked"
    nested.mkdir(parents=True)
    (nested / "db.json").write_text("private-bytes")
    library = Path("scripts/windows/Repair-TesterLabPortableAcl.ps1").absolute()
    script = tmp_path / "walk.ps1"
    script.write_text(
        r"""
param($Library,$Root,$Id,$Receipt)
$ErrorActionPreference='Stop'
. $Library -FunctionsOnly
$LeafPath=Join-Path $Root 'private/locked/db.json'
$LeafAcl=Get-Acl -LiteralPath $LeafPath
$LeafAcl.SetAccessRuleProtection($true,$true)
[IO.File]::SetAccessControl($LeafPath,$LeafAcl)
$OriginalLeaf=(Get-Acl -LiteralPath $LeafPath).Sddl
foreach ($Name in @('private/locked','private')) {
 $Path=Join-Path $Root $Name
 $Acl=Get-Acl -LiteralPath $Path
 $Acl.SetAccessRuleProtection($true,$false)
 foreach ($Rule in @($Acl.Access)) {$null=$Acl.RemoveAccessRuleSpecific($Rule)}
 [IO.Directory]::SetAccessControl($Path,$Acl)
 if ($Name -eq 'private/locked') {$OriginalLocked=(Get-Acl -LiteralPath $Path).Sddl}
}
$Denied=$false
try {$null=Get-ChildItem -LiteralPath (Join-Path $Root 'private') -ErrorAction Stop} catch [UnauthorizedAccessException] {$Denied=$true}
if (-not $Denied) {throw 'Locked directory fixture did not deny enumeration'}
Invoke-TesterLabAclRecovery -Root $Root -InstallationId $Id -ReceiptPath $Receipt -VerifyNoServices {}
if ((Get-Content -LiteralPath (Join-Path $Root 'private/locked/db.json') -Raw) -ne 'private-bytes') {throw 'Bytes changed'}
$Records=@(Get-Content -LiteralPath ($Receipt+'.jsonl') | ForEach-Object {$_|ConvertFrom-Json})
$Snapshots=@($Records | Where-Object {$_.kind -eq 'snapshot'})
if ($Snapshots.Count -lt 5) {throw 'Snapshots absent'}
$Locked=@($Snapshots | Where-Object {$_.entry.path -like '*private\locked'})
if ($Locked.Count -ne 1 -or $Locked[0].entry.original_sddl -ne $OriginalLocked) {throw 'Original locked descriptor was not preserved'}
$Leaf=@($Snapshots | Where-Object {$_.entry.path -like '*private\locked\db.json'})
if ($Leaf.Count -ne 1 -or $Leaf[0].entry.original_sddl -ne $OriginalLeaf) {throw 'Unseen child descriptor changed before snapshot'}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-File",
            str(script),
            str(library),
            str(root),
            str(ident),
            str(tmp_path / "receipt.json"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_walk_never_traverses_foreign_directory_or_junction(tmp_path):
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
    foreign = root / "runtime/foreign"
    foreign.mkdir(parents=True)
    (foreign / "leaf.txt").write_text("foreign-child")
    blocked = root / "runtime/blocked"
    blocked.mkdir()
    (blocked / "leaf.txt").write_text("blocked-child")
    target = tmp_path / "target"
    target.mkdir()
    (target / "untouched.txt").write_text("keep")
    link = root / "runtime/junction"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True
    )
    library = Path("scripts/windows/Repair-TesterLabPortableAcl.ps1").absolute()
    script = tmp_path / "skip.ps1"
    script.write_text(
        r"""
param($Library,$Root,$Id,$Receipt)
$ErrorActionPreference='Stop'
. $Library -FunctionsOnly
$ForeignLeaf=Join-Path $Root 'runtime/foreign/leaf.txt'
$Before=(Get-Acl -LiteralPath $ForeignLeaf).Sddl
function Get-Acl {
 param([string]$LiteralPath)
 if ($LiteralPath.EndsWith('\blocked')) {throw [UnauthorizedAccessException]::new('fixture denied')}
 $Acl=Microsoft.PowerShell.Security\Get-Acl -LiteralPath $LiteralPath
 if ($LiteralPath.EndsWith('\foreign')) {$Acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-18'))}
 return $Acl
}
Invoke-TesterLabAclRecovery -Root $Root -InstallationId $Id -ReceiptPath $Receipt -VerifyNoServices {}
if ((Get-Acl -LiteralPath $ForeignLeaf).Sddl -ne $Before) {throw ('Foreign child ACL changed BEFORE='+$Before+' AFTER='+(Get-Acl -LiteralPath $ForeignLeaf).Sddl)}
$Snapshots=@(Get-Content -LiteralPath ($Receipt+'.jsonl') | ForEach-Object {$_|ConvertFrom-Json} | Where-Object {$_.kind -eq 'snapshot'})
if (-not @($Snapshots | Where-Object {$_.entry.skip -eq 'foreign-owner'}).Count) {throw 'Foreign directory not skipped'}
if (-not @($Snapshots | Where-Object {$_.entry.skip -eq 'reparse-path'}).Count) {throw 'Junction not skipped'}
if (-not @($Snapshots | Where-Object {$_.entry.skip -eq 'acl-unreadable'}).Count) {throw 'Unreadable directory not skipped'}
if (@($Snapshots | Where-Object {$_.entry.path -eq $ForeignLeaf}).Count) {throw 'Traversed foreign directory'}
""",
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-File",
                str(script),
                str(library),
                str(root),
                str(ident),
                str(tmp_path / "receipt.json"),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert (target / "untouched.txt").read_text() == "keep"
    finally:
        link.rmdir()
