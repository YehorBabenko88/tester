# Run this exact published script manually from an administrator PowerShell.
# No automatic RunAs, downloaded execution, installer adoption, or data deletion.
[CmdletBinding()]
param(
    [ValidateSet('install','start','stop','restart','status','remove')][string]$Action='status',
    [string]$ManifestSHA256=''
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$LabRoot='C:\TesterLab'
$Identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$Principal=[Security.Principal.WindowsPrincipal]::new($Identity)
if ($Action -ne 'status' -and -not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Open an administrator shell explicitly after reviewing and approving the published SHA and script hash.'
}
$MarkerPath=Join-Path $LabRoot '.tester-lab.json'
$Marker=Get-Content -LiteralPath $MarkerPath -Raw | ConvertFrom-Json
if ($Marker.schema -ne 'tester-lab/v1' -or $Marker.root -ne $LabRoot -or $Marker.active_sha -notmatch '^[0-9a-f]{40}$') {
    throw 'Unknown fresh lab ownership marker.'
}
$Release=Join-Path $LabRoot ('releases\'+$Marker.active_sha)
if ($PSCommandPath -ne (Join-Path $Release 'scripts\windows\Manage-TesterLab.ps1')) {
    throw 'Execute the exact published active-release script.'
}
function Assert-LabPlain([string]$Path) {
    $Item=Get-Item -LiteralPath $Path -Force
    while ($null -ne $Item) {
        if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse path refused.' }
        if ($Item -is [IO.FileInfo]) { $Item=$Item.Directory } else { $Item=$Item.Parent }
    }
}
Assert-LabPlain $LabRoot
Assert-LabPlain $MarkerPath
Assert-LabPlain $Release
if ($Action -ne 'status') {
    if ($ManifestSHA256 -notmatch '^[0-9a-fA-F]{64}$') { throw 'Explicit externally approved -ManifestSHA256 is required.' }
    $PayloadPath=Join-Path $LabRoot 'state\deploy-payload.json'
    Assert-LabPlain $PayloadPath
    if ((Get-FileHash -LiteralPath $PayloadPath -Algorithm SHA256).Hash -ne $ManifestSHA256) {
        throw 'External payload manifest hash differs from approved value.'
    }
    $Payload=Get-Content -LiteralPath $PayloadPath -Raw | ConvertFrom-Json
    if ($Payload.schema_version -ne 'tester-lab-payload/v1' -or $Payload.source_sha -ne $Marker.active_sha) {
        throw 'Payload source differs from active published SHA.'
    }
    $Expected=@{}
    foreach ($Property in $Payload.files.PSObject.Properties) {
        $Name=$Property.Name
        if ($Name -match '(^/|\\|:|(^|/)\.\.?(/|$)|//)' -or $Property.Value -notmatch '^[0-9a-f]{64}$') {
            throw 'Unsafe payload inventory entry.'
        }
        $Expected[$Name]=$Property.Value
    }
    if ($Expected.Count -eq 0) { throw 'Empty payload inventory.' }
    $Entries=@(Get-ChildItem -LiteralPath $Release -Recurse -Force)
    foreach ($Entry in $Entries) { Assert-LabPlain $Entry.FullName }
    $Files=@($Entries | Where-Object { -not $_.PSIsContainer })
    if ($Files.Count -ne $Expected.Count) { throw 'Published release contains missing or extra files.' }
    foreach ($File in $Files) {
        Assert-LabPlain $File.FullName
        $Relative=$File.FullName.Substring($Release.Length+1).Replace('\','/')
        if (-not $Expected.ContainsKey($Relative) -or (Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash -ne $Expected[$Relative]) {
            throw 'Published release inventory mismatch.'
        }
    }
}
$Python=Join-Path $LabRoot 'dependencies\venv\Scripts\python.exe'
# Validate every release byte/reparse path before changing permissions or SCM.
& $Python -I -B -c 'import sys; sys.path.insert(0,sys.argv[1]); from src.api.lab_install import check; check(sys.argv[2])' $Release $LabRoot
if ($LASTEXITCODE -ne 0) { throw 'Published payload/ownership verification failed.' }
# This helper is part of the independently verified published payload.
. (Join-Path $PSScriptRoot 'Repair-TesterLabPortableAcl.ps1') -FunctionsOnly
function Set-LabAcl([string]$Path,[string]$CreatorRights,[string]$ServiceRights,[switch]$Children) {
    $Items=@(Get-Item -LiteralPath $Path -Force)
    if ($Children) { $Items+=@(Get-ChildItem -LiteralPath $Path -Recurse -Force) }
    foreach ($Item in $Items) {
        Set-TesterLabExplicitAcl -ItemPath $Item.FullName -CreatorSid $CreatorSid -CreatorRights $CreatorRights -ServiceRights $ServiceRights
    }
}
if ($Action -eq 'install') {
    $CreatorName=(Get-Acl -LiteralPath $LabRoot).Owner
    $CreatorSid=([Security.Principal.NTAccount]::new($CreatorName)).Translate([Security.Principal.SecurityIdentifier]).Value
    if ($CreatorSid -in @('S-1-5-32-544','S-1-5-18','S-1-5-19')) {
        throw 'Lab root owner must identify the original individual creator account before ACL installation.'
    }
    # Refuse any reparse descendant before recursive scoped ACL operations.
    $Candidates=@(Get-Item -LiteralPath $LabRoot)+@(Get-ChildItem -LiteralPath $LabRoot -Recurse -Force)
    foreach ($Item in $Candidates) {
        if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse root/descendant refused.' }
    }
    Set-LabAcl $LabRoot 'ReadAndExecute' 'ReadAndExecute'
    foreach ($Name in @('releases','dependencies','state')) {
        Set-LabAcl (Join-Path $LabRoot $Name) 'ReadAndExecute' 'ReadAndExecute' -Children
    }
    Set-LabAcl (Join-Path $LabRoot 'runtime') 'ReadAndExecute' 'Modify' -Children
    Set-LabAcl (Join-Path $LabRoot 'postgres-data') '' 'Modify' -Children
    foreach ($Name in @('private','backups')) {
        Set-LabAcl (Join-Path $LabRoot $Name) '' '' -Children
    }
    Set-LabAcl (Join-Path $LabRoot 'config.json') 'Read' 'Read'
    Set-LabAcl $MarkerPath 'Read' 'Read'

}
& $Python -I -B (Join-Path $Release 'scripts\windows\lab_service.py') $Action --root $LabRoot
if ($LASTEXITCODE -ne 0) { throw 'Fresh lab SCM action failed; preserve state and inspect evidence.' }
