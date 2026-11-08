# One separately approved repair. Never elevates itself or retries a failed UAC.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$RepairManifest,
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-fA-F]{64}$')][string]$RepairManifestSHA256
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$Root='C:\TesterLab'
$AppSHA='a3138eb9813832e9a839a99e54547001343ab3fd'
$DeploySHA='21b2854d02ac1d27563cd263e89504a13d4a6eb1724d3681588e63803eea506c'
$Id='89189096-be4c-4cb3-bf2b-07b44f25fd60'
$Release=Join-Path $Root ('releases\'+$AppSHA)
$Python=Join-Path $Root 'dependencies\venv\Scripts\python.exe'
$Manager=Join-Path $Release 'scripts\windows\Manage-TesterLab.ps1'
$Report=[ordered]@{kind='NATIVE_HOST_REPAIR';status='FAILED';stage='PREFLIGHT';application_source_sha=$AppSHA;packager_source_sha=$null;checks=@();error_type=$null}
$ReportAllowed=$false
function Assert-Plain([string]$Path) {
    $Item=Get-Item -LiteralPath $Path -Force
    while ($null -ne $Item) {
        if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {throw 'Reparse path refused.'}
        if ($Item -is [IO.FileInfo]) {$Item=$Item.Directory} else {$Item=$Item.Parent}
    }
}
function Invoke-Owned([string]$Action) {
    & $Manager -Action $Action -ManifestSHA256 $DeploySHA
    if ($LASTEXITCODE -ne 0) {throw 'Owned service action failed.'}
}
function Assert-Ready {
    $Deadline=[DateTime]::UtcNow.AddSeconds(10)
    do {
        try {
            $Native=Get-Content -LiteralPath (Join-Path $Root 'runtime\service.json') -Raw|ConvertFrom-Json
            $Health=Invoke-RestMethod -Uri 'http://127.0.0.1:18766/api/health' -TimeoutSec 3
            if ($Health.data.status -eq 'ok' -and $Health.data.instance_id -eq $Native.instance_id -and $Native.source_sha -eq $AppSHA) {
                $Report.checks+=@{source_sha=$Native.source_sha;instance_id=$Native.instance_id;supervisor_pid=$Native.supervisor_pid;health='ok'}
                return
            }
        } catch {}
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $Deadline)
    throw 'Native readiness/instance/source mismatch.'
}
$Principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {throw 'Requires the new explicitly approved administrator action.'}
try {
    Assert-Plain $RepairManifest
    if ((Get-FileHash -LiteralPath $RepairManifest -Algorithm SHA256).Hash -ne $RepairManifestSHA256) {throw 'Repair manifest hash mismatch.'}
    $Patch=Get-Content -LiteralPath $RepairManifest -Raw|ConvertFrom-Json
    if ($Patch.schema -ne 'tester-native-host-repair/v1' -or $Patch.application_source_sha -ne $AppSHA -or $Patch.installation_id -ne $Id -or $Patch.application_deploy_manifest_sha256 -ne $DeploySHA) {throw 'Wrong repair/application identity.'}
    if ($Patch.packager_source_sha -notmatch '^[0-9a-f]{40}$' -or (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash -ne $Patch.repair_script_sha256) {throw 'Published repair source mismatch.'}
    $Report.packager_source_sha=$Patch.packager_source_sha
    $Owner=Get-Content -LiteralPath (Join-Path $Root '.tester-lab.json') -Raw|ConvertFrom-Json
    if ($Owner.schema -ne 'tester-lab/v1' -or $Owner.repository -ne 'YehorBabenko88/tester' -or $Owner.installation_id -ne $Id -or $Owner.active_sha -ne $AppSHA -or $Owner.root -ne $Root) {throw 'Wrong owned lab.'}
    if ($Owner.services.app -ne 'TesterLabApp_89189096be4c4cb3bf2b07b44f25fd60' -or $Owner.services.postgres -ne 'TesterLabPG_89189096be4c4cb3bf2b07b44f25fd60') {throw 'Foreign service identities.'}
    $App=Get-CimInstance Win32_Service -Filter "Name='$($Owner.services.app)'"
    $PG=Get-CimInstance Win32_Service -Filter "Name='$($Owner.services.postgres)'"
    if ($App.State -ne 'Stopped' -or $PG.State -ne 'Running' -or $App.StartName -ne 'NT AUTHORITY\LocalService' -or $PG.StartName -ne 'NT AUTHORITY\LocalService') {throw 'Expected stopped owned app and running owned PG.'}
    $Before=Get-CimInstance Win32_Service -Filter "Name='postgresql-x64-18'"|Select-Object Name,State,ProcessId,PathName
    # Verify source externally before importing even the installed Python controller.
    $SourceManifest=Join-Path $Root 'state\deploy-payload.json'
    Assert-Plain $SourceManifest
    if ((Get-FileHash -LiteralPath $SourceManifest -Algorithm SHA256).Hash -ne $DeploySHA) {throw 'Installed source manifest mismatch.'}
    $Source=Get-Content -LiteralPath $SourceManifest -Raw|ConvertFrom-Json
    if ($Source.source_sha -ne $AppSHA) {throw 'Source pin mismatch.'}
    $SourceFiles=@(Get-ChildItem -LiteralPath $Release -Recurse -Force)
    foreach ($Item in $SourceFiles) {Assert-Plain $Item.FullName}
    if (@($SourceFiles|Where-Object {-not $_.PSIsContainer}).Count -ne @($Source.files.PSObject.Properties).Count) {throw 'Unexpected source inventory.'}
    foreach ($Property in $Source.files.PSObject.Properties) {
        if ($Property.Name -match '(^/|\\|:|(^|/)\.\.?(/|$)|//)') {throw 'Unsafe source inventory.'}
        if ((Get-FileHash -LiteralPath (Join-Path $Release $Property.Name) -Algorithm SHA256).Hash -ne $Property.Value) {throw 'Installed source byte mismatch.'}
    }
    Invoke-Owned 'status'
    Assert-Plain (Join-Path $Root 'runtime')
    Assert-Plain (Join-Path $Root 'backups')
    $ReportAllowed=$true
    $OldManifest=Join-Path $Root 'state\service-runtime.json'
    Assert-Plain $OldManifest
    if ((Get-FileHash -LiteralPath $OldManifest -Algorithm SHA256).Hash -ne $Patch.old_runtime_manifest_sha256) {throw 'Old native package differs; no blind repair.'}
    $Old=Get-Content -LiteralPath $OldManifest -Raw|ConvertFrom-Json
    $Runtime=Join-Path $Root 'dependencies\service'
    Assert-Plain $Runtime
    $Actual=@(Get-ChildItem -LiteralPath $Runtime -Recurse -Force)
    foreach ($Item in $Actual) {Assert-Plain $Item.FullName}
    $Files=@($Actual|Where-Object {-not $_.PSIsContainer})
    if ($Files.Count -ne @($Old.files.PSObject.Properties).Count) {throw 'Unexpected old native files.'}
    foreach ($Property in $Old.files.PSObject.Properties) {
        if ($Property.Name -match '(^/|\\|:|(^|/)\.\.?(/|$)|//)') {throw 'Unsafe native inventory.'}
        if ((Get-FileHash -LiteralPath (Join-Path $Runtime $Property.Name) -Algorithm SHA256).Hash -ne $Property.Value) {throw 'Old native byte mismatch.'}
    }
    $Module=Join-Path $Root 'dependencies\venv\Lib\site-packages\win32\servicemanager.pyd'
    $Destination=Join-Path $Runtime 'servicemanager.pyd'
    $Packages=Join-Path $Runtime 'Lib\site-packages'
    if (Test-Path -LiteralPath $Packages) {Assert-Plain $Packages}
    $Pth=Join-Path $Packages 'tester-lab.pth'
    if (Test-Path -LiteralPath $Pth) {throw 'Path configuration already present; never replay.'}
    $ExpectedPth=(@($Runtime,$Release,(Join-Path $Root 'dependencies\venv\Lib\site-packages'),(Join-Path $Root 'dependencies\venv\Lib\site-packages\win32'),(Join-Path $Root 'dependencies\venv\Lib\site-packages\win32\lib')) -join "`n")+"`n"
    $PthBytes=[Text.UTF8Encoding]::new($false).GetBytes($ExpectedPth)
    $Hasher=[Security.Cryptography.SHA256]::Create()
    try {$PthHash=([BitConverter]::ToString($Hasher.ComputeHash($PthBytes))).Replace('-','').ToLowerInvariant()} finally {$Hasher.Dispose()}
    if ($PthHash -ne $Patch.pth_sha256 -or $Patch.python_class -cne (Join-Path $Runtime 'lab_service_bootstrap.LabService')) {throw 'Unexpected path bootstrap.'}
    if (Test-Path -LiteralPath $Destination) {throw 'Module already present; never replay this repair.'}
    Assert-Plain $Module
    if ((Get-FileHash -LiteralPath $Module -Algorithm SHA256).Hash -ne $Patch.servicemanager_sha256) {throw 'Module differs from locked wheel.'}
    $NewManifest=Join-Path ([IO.Path]::GetDirectoryName($RepairManifest)) 'native-host-runtime-fixed.json'
    Assert-Plain $NewManifest
    if ((Get-FileHash -LiteralPath $NewManifest -Algorithm SHA256).Hash -ne $Patch.new_runtime_manifest_sha256) {throw 'New native manifest mismatch.'}
    $New=Get-Content -LiteralPath $NewManifest -Raw|ConvertFrom-Json
    if (@($New.files.PSObject.Properties).Count -ne @($Old.files.PSObject.Properties).Count+2 -or $New.files.'servicemanager.pyd' -ne $Patch.servicemanager_sha256 -or $New.files.'Lib/site-packages/tester-lab.pth' -ne $PthHash) {throw 'Repair must add exactly the pinned module and plain path configuration.'}
    foreach ($Property in $Old.files.PSObject.Properties) {if ($New.files.($Property.Name) -ne $Property.Value) {throw 'Repair would change old native bytes.'}}
    $Report.stage='BACKUP'
    & $Python -I -B (Join-Path $Release 'scripts\lab_portable.py') backup-restore
    if ($LASTEXITCODE -ne 0) {throw 'Owned backup/restore failed.'}
    $Report.stage='STOP_OWNED'
    Invoke-Owned 'stop'
    $Report.stage='PATCH_NATIVE_PACKAGE'
    $Backup=Join-Path $Root 'backups\native-manifest-before-manager-repair.json'
    Assert-Plain ([IO.Path]::GetDirectoryName($Backup))
    if (Test-Path -LiteralPath $Backup) {throw 'Never overwrite a prior native repair backup.'}
    Assert-Plain $OldManifest
    [IO.File]::Copy($OldManifest,$Backup,$false)
    $ModuleBytes=[IO.File]::ReadAllBytes($Module)
    $SHA=[Security.Cryptography.SHA256]::Create()
    try {$ActualModuleHash=([BitConverter]::ToString($SHA.ComputeHash($ModuleBytes))).Replace('-','').ToLowerInvariant()} finally {$SHA.Dispose()}
    if ($ActualModuleHash -ne $Patch.servicemanager_sha256) {throw 'Module changed during read.'}
    $File=[IO.File]::Open($Destination,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try {$File.Write($ModuleBytes,0,$ModuleBytes.Length);$File.Flush($true)} finally {$File.Dispose()}
    # The new DLL is readable by LocalService; no global DLL registration.
    $ACL=Get-Acl -LiteralPath $Destination
    $ACL.SetAccessRuleProtection($true,$false)
    foreach ($Rule in @($ACL.Access)) {$null=$ACL.RemoveAccessRuleSpecific($Rule)}
    foreach ($Sid in @('S-1-5-32-544','S-1-5-18')) {$ACL.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($Sid),'FullControl','Allow'))}
    $Creator=(Get-Acl -LiteralPath $Root).GetOwner([Security.Principal.SecurityIdentifier])
    foreach ($Sid in @($Creator,[Security.Principal.SecurityIdentifier]::new('S-1-5-19'))) {$ACL.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($Sid,'ReadAndExecute','Allow'))}
    [IO.File]::SetAccessControl($Destination,$ACL)
    if (-not (Test-Path -LiteralPath $Packages)) {$null=New-Item -ItemType Directory -Path $Packages}
    Assert-Plain $Packages
    $File=[IO.File]::Open($Pth,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try {$File.Write($PthBytes,0,$PthBytes.Length);$File.Flush($true)} finally {$File.Dispose()}
    [IO.File]::SetAccessControl($Pth,$ACL)
    Assert-Plain $OldManifest
    Assert-Plain $NewManifest
    Copy-Item -LiteralPath $NewManifest -Destination $OldManifest
    $Report.stage='REMOVE_REINSTALL_RETAIN_DATA'
    # Native deregistration does not archive the installation marker or remove data.
    & $Python -I -B -c 'import sys;sys.path.insert(0,sys.argv[1]);from pathlib import Path;from src.api.lab_scm import NativeSCM,apply;s=NativeSCM();apply(sys.argv[3],Path(sys.argv[2]),s);s.close()' $Release $Root remove
    if ($LASTEXITCODE -ne 0) {throw 'Owned deregistration failed.'}
    Invoke-Owned 'install'
    # Vendor-supported absolute [path\]module.Class; only this owned app key.
    $ClassKey='SYSTEM\CurrentControlSet\Services\'+$Owner.services.app+'\PythonClass'
    $Key=[Microsoft.Win32.Registry]::LocalMachine.OpenSubKey($ClassKey,$true)
    try {$Key.SetValue('',[string]$Patch.python_class,[Microsoft.Win32.RegistryValueKind]::String)} finally {$Key.Dispose()}
    $Report.stage='FIRST_START'
    Invoke-Owned 'start'
    Assert-Ready
    $Report.stage='STOP_START'
    Invoke-Owned 'stop'
    Invoke-Owned 'start'
    Assert-Ready
    $After=Get-CimInstance Win32_Service -Filter "Name='postgresql-x64-18'"|Select-Object Name,State,ProcessId,PathName
    if (($Before|ConvertTo-Json -Compress) -cne ($After|ConvertTo-Json -Compress)) {throw 'Existing PG5432 service observation changed.'}
    $Report.stage='COMPLETE'
    $Report.status='NATIVE_REPAIR_REMOVE_REINSTALL_START_STOP_START_PASS'
} catch {
    $Report.error_type=$_.Exception.GetType().FullName
    throw
} finally {
    if ($ReportAllowed) {
        Assert-Plain (Join-Path $Root 'runtime')
        $Report.completed_at=[DateTime]::UtcNow.ToString('o')
        $Path=Join-Path $Root ('runtime\native-host-repair-'+[Guid]::NewGuid().ToString('N')+'.json')
        $Bytes=[Text.UTF8Encoding]::new($false).GetBytes(($Report|ConvertTo-Json -Depth 8))
        $File=[IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        try {$File.Write($Bytes,0,$Bytes.Length);$File.Flush($true)} finally {$File.Dispose()}
    }
}
