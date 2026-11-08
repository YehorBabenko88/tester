# Published, separately approved in-place app upgrade. Never self-elevates.
[CmdletBinding()]
param([switch]$FunctionsOnly,[string]$UpgradeManifest='',
      [ValidatePattern('^$|^[0-9a-fA-F]{64}$')][string]$UpgradeManifestSHA256='')
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
function Assert-UpgradePlain([string]$Path) {
    $Item=Get-Item -LiteralPath $Path -Force
    while ($null -ne $Item) {
        if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {throw 'Reparse path refused.'}
        if ($Item -is [IO.FileInfo]) {$Item=$Item.Directory} else {$Item=$Item.Parent}
    }
}
function Assert-UpgradeHash([string]$Path,[string]$Digest) {
    Assert-UpgradePlain $Path
    if ($Digest -notmatch '^[0-9a-fA-F]{64}$' -or (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ine $Digest) {throw 'Approved file hash mismatch.'}
}
function Assert-UpgradeTree([string]$Directory,$Inventory) {
    Assert-UpgradePlain $Directory
    $Expected=@{}
    foreach ($Entry in $Inventory.PSObject.Properties) {
        if ($Entry.Name -match '(^/|\\|:|(^|/)\.\.?(/|$)|//)' -or $Entry.Value -notmatch '^[0-9a-fA-F]{64}$') {throw 'Unsafe inventory entry.'}
        $Expected[$Entry.Name]=$Entry.Value
    }
    if ($Expected.Count -eq 0) {throw 'Empty inventory.'}
    $Actual=@(Get-ChildItem -LiteralPath $Directory -Recurse -Force)
    foreach ($Item in $Actual) {Assert-UpgradePlain $Item.FullName}
    $Files=@($Actual | Where-Object {-not $_.PSIsContainer})
    if ($Files.Count -ne $Expected.Count) {throw 'Missing or extra inventory files.'}
    foreach ($File in $Files) {
        $Relative=$File.FullName.Substring($Directory.Length+1).Replace('\','/')
        if (-not $Expected.ContainsKey($Relative)) {throw 'Unknown inventory file.'}
        Assert-UpgradeHash $File.FullName $Expected[$Relative]
    }
}
function Assert-UpgradeNativeDiff($Old,$New) {
    $Keys=@($Old.PSObject.Properties.Name)+@($New.PSObject.Properties.Name) | Select-Object -Unique
    foreach ($Name in $Keys) {
        $Before=$Old.PSObject.Properties[$Name];$After=$New.PSObject.Properties[$Name]
        if ($null -ne $Before -and $null -ne $After -and $Before.Value -ceq $After.Value) {continue}
        if ($Name -notmatch '^(Lib/site-packages/tester-lab\.pth|lab_service_bootstrap\.py|__pycache__/lab_service_bootstrap\.cpython-[0-9]+\.pyc|Lib/sitecustomize\.py|Lib/__pycache__/sitecustomize\.cpython-[0-9]+\.pyc)$') {throw 'Unrelated native runtime change refused.'}
        if ($null -eq $After) {throw 'Native startup asset removal refused.'}
    }
}
function Copy-UpgradeCandidate([string]$Source,[string]$Candidate,$Inventory) {
    if (Test-Path -LiteralPath $Candidate) {throw 'Candidate must be new.'}
    $null=New-Item -ItemType Directory -Path $Candidate
    foreach ($Item in @(Get-ChildItem -LiteralPath $Source -Force)) {Copy-Item -LiteralPath $Item.FullName -Destination $Candidate -Recurse -Force}
    Assert-UpgradeTree $Candidate $Inventory
}
function Write-UpgradeAtomicBytes([string]$Target,[byte[]]$Bytes,[string]$WorkspaceParent) {
    Assert-UpgradePlain $Target
    Assert-UpgradePlain $WorkspaceParent
    $Temporary=Join-Path $WorkspaceParent ('.tester-upgrade-metadata-'+[Guid]::NewGuid().ToString('N'))
    $File=[IO.File]::Open($Temporary,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try {$File.Write($Bytes,0,$Bytes.Length);$File.Flush($true)} finally {$File.Dispose()}
    [IO.File]::Replace($Temporary,$Target,$null)
}
function Assert-UpgradeBackupDestination([string]$BackupRoot,[string]$Bundle) {
    Assert-UpgradePlain $BackupRoot
    if ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($Bundle)) -ine [IO.Path]::GetFullPath($BackupRoot) -or [IO.Path]::GetFileName($Bundle) -notmatch '^upgrade-[0-9a-f]{32}$') {throw 'Backup bundle escaped its fixed parent.'}
    if (Test-Path -LiteralPath $Bundle) {Assert-UpgradePlain $Bundle}
}
function Get-UpgradeRegistryChanges([string]$Base,[string]$SHA,[string]$Class,[string]$PythonPath) {
    [pscustomobject]@{path=$Base+'\Parameters';name='SourceSHA';value=$SHA}
    [pscustomobject]@{path=$Base+'\PythonClass';name='';value=$Class}
    [pscustomobject]@{path=$Base+'\PythonPath';name='';value=$PythonPath}
}
function Set-UpgradeAppRegistry([string]$SHA,[string]$Class,[string]$PythonPath) {
    $Base='SYSTEM\CurrentControlSet\Services\'+$script:Owner.services.app
    foreach ($Change in @(Get-UpgradeRegistryChanges $Base $SHA $Class $PythonPath)) {
        $Key=[Microsoft.Win32.Registry]::LocalMachine.OpenSubKey($Change.path,$true)
        if ($null -eq $Key) {throw 'Expected existing owned app registry key.'}
        try {$Key.SetValue($Change.name,[string]$Change.value,[Microsoft.Win32.RegistryValueKind]::String)} finally {$Key.Dispose()}
    }
}
function Assert-UpgradeOwnedServices([string[]]$AllowedSHA) {
    $App=Get-CimInstance Win32_Service -Filter "Name='$($script:Owner.services.app)'"
    $PG=Get-CimInstance Win32_Service -Filter "Name='$($script:Owner.services.postgres)'"
    $PGImage='"C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe" runservice -N '+$script:Owner.services.postgres+' -D C:\TesterLab\postgres-data -w'
    if ($null -eq $App -or $null -eq $PG -or $App.PathName -cne (Join-Path $script:Root 'dependencies\service\pythonservice.exe') -or $PG.PathName -cne $PGImage -or $App.StartName -ine 'NT AUTHORITY\LocalService' -or $PG.StartName -ine 'NT AUTHORITY\LocalService' -or $PG.State -ne 'Running') {throw 'Foreign or unavailable owned services.'}
    foreach ($Name in @($script:Owner.services.app,$script:Owner.services.postgres)) {
        $Parameters=Get-ItemProperty -LiteralPath ('Registry::HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\'+$Name+'\Parameters')
        if ($Parameters.InstallationId -ne $script:Id -or $Parameters.Root -cne $script:Root) {throw 'Foreign registry ownership.'}
        if ($Name -eq $script:Owner.services.app -and $Parameters.SourceSHA -notin $AllowedSHA) {throw 'Unexpected registered app source.'}
    }
    if ($script:PGPid -ne 0 -and [int]$PG.ProcessId -ne $script:PGPid) {throw 'Dedicated PG process changed during app-only upgrade.'}
    return $App
}
function Invoke-UpgradeAppAction([string]$Action,[string[]]$AllowedSHA) {
    $null=Assert-UpgradeOwnedServices $AllowedSHA
    $Service=Get-Service -Name $script:Owner.services.app
    if ($Action -eq 'stop') {if ($Service.Status -ne 'Stopped') {Stop-Service -InputObject $Service};$Service.WaitForStatus('Stopped',[TimeSpan]::FromSeconds(65))}
    elseif ($Action -eq 'start') {Start-Service -InputObject $Service;$Service.WaitForStatus('Running',[TimeSpan]::FromSeconds(65))}
    else {throw 'Unknown app action.'}
}
function Assert-UpgradeReady([string]$SHA,[string]$PayloadDigest,[bool]$RequireIdentity) {
    $Deadline=[DateTime]::UtcNow.AddSeconds(20)
    do {
        try {
            $Native=Get-Content -LiteralPath (Join-Path $script:Root 'runtime\service.json') -Raw | ConvertFrom-Json
            $Health=Invoke-RestMethod -Uri 'http://127.0.0.1:18766/api/health' -TimeoutSec 3
            if ($Native.source_sha -eq $SHA -and $Health.data.status -eq 'ok' -and $Health.data.instance_id -eq $Native.instance_id) {
                if ($RequireIdentity) {
                    $Identity=Invoke-RestMethod -Uri 'http://127.0.0.1:18766/api/lab/identity' -TimeoutSec 3
                    if ($Identity.data.source_sha -ne $SHA -or $Identity.data.tree_sha -ne $script:NewPayload.tree_sha -or $Identity.data.installation_id -ne $script:Id -or $Identity.data.deploy_manifest_sha256 -ne $PayloadDigest -or $Identity.data.instance_id -ne $Native.instance_id -or $Identity.data.services.app -ne $script:Owner.services.app -or $Identity.data.services.postgres -ne $script:Owner.services.postgres) {throw 'API build identity differs.'}
                }
                return
            }
        } catch {}
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $Deadline)
    throw 'Service/API source and instance readiness failed.'
}
if ($FunctionsOnly) {return}
$Root='C:\TesterLab';$Id='89189096-be4c-4cb3-bf2b-07b44f25fd60';$PGPid=0
$Workspace='C:\Users\Administrator\Documents\Codex\2026-11-08\files-pasted-by-the-user-codex\work'
$Principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {throw 'Requires a separately approved administrator action; never elevates itself.'}
if (-not $UpgradeManifest -or -not $UpgradeManifestSHA256) {throw 'Explicit approved upgrade manifest and SHA256 are required.'}
Assert-UpgradeHash $UpgradeManifest $UpgradeManifestSHA256
$Plan=Get-Content -LiteralPath $UpgradeManifest -Raw | ConvertFrom-Json
if ($Plan.schema -ne 'tester-lab-upgrade/v1' -or $Plan.installation_id -ne $Id -or $Plan.old_source_sha -notmatch '^[0-9a-f]{40}$' -or $Plan.new_source_sha -notmatch '^[0-9a-f]{40}$' -or $Plan.old_source_sha -eq $Plan.new_source_sha) {throw 'Wrong upgrade scope.'}
Assert-UpgradeHash $PSCommandPath $Plan.script_sha256
$NewSource=[IO.Path]::GetFullPath($Plan.new_source_root).TrimEnd('\')
$NewRuntime=[IO.Path]::GetFullPath($Plan.new_runtime_root).TrimEnd('\')
if ($NewSource -ine (Join-Path $Workspace ('published\'+$Plan.new_source_sha)) -or $NewRuntime -ine (Join-Path $Workspace ('native-upgrade-'+$Plan.new_source_sha))) {throw 'New source/native assets must use the fixed published SHA paths.'}
foreach ($Path in @($NewSource,$NewRuntime)) {
    if (-not $Path.StartsWith($Workspace+'\',[StringComparison]::OrdinalIgnoreCase) -or [IO.Path]::GetPathRoot($Path) -ine [IO.Path]::GetPathRoot($Root)) {throw 'New assets must be same-volume published workspace assets.'}
    Assert-UpgradePlain $Path
}
if ($PSCommandPath -cne (Join-Path $NewSource 'scripts\windows\Update-TesterLabRelease.ps1')) {throw 'Run the exact published new source entrypoint.'}
$MarkerPath=Join-Path $Root '.tester-lab.json';Assert-UpgradePlain $MarkerPath
$Owner=Get-Content -LiteralPath $MarkerPath -Raw | ConvertFrom-Json
if ($Owner.schema -ne 'tester-lab/v1' -or $Owner.root -cne $Root -or $Owner.repository -ne 'YehorBabenko88/tester' -or $Owner.installation_id -ne $Id -or $Owner.active_sha -ne $Plan.old_source_sha -or $Owner.services.app -ne ('TesterLabApp_'+$Id.Replace('-','')) -or $Owner.services.postgres -ne ('TesterLabPG_'+$Id.Replace('-',''))) {throw 'Unexpected existing lab ownership/source.'}
$OldRelease=Join-Path $Root ('releases\'+$Plan.old_source_sha)
$NewRelease=Join-Path $Root ('releases\'+$Plan.new_source_sha)
$PayloadPath=Join-Path $Root 'state\deploy-payload.json'
$RuntimeManifest=Join-Path $Root 'state\service-runtime.json'
Assert-UpgradeHash $PayloadPath $Plan.old_payload_sha256
Assert-UpgradeHash $Plan.new_payload_path $Plan.new_payload_sha256
Assert-UpgradeHash $RuntimeManifest $Plan.old_runtime_manifest_sha256
Assert-UpgradeHash $Plan.new_runtime_manifest_path $Plan.new_runtime_manifest_sha256
$OldPayload=Get-Content -LiteralPath $PayloadPath -Raw|ConvertFrom-Json
$NewPayload=Get-Content -LiteralPath $Plan.new_payload_path -Raw|ConvertFrom-Json
$OldNative=Get-Content -LiteralPath $RuntimeManifest -Raw|ConvertFrom-Json
$NewNative=Get-Content -LiteralPath $Plan.new_runtime_manifest_path -Raw|ConvertFrom-Json
if ($OldPayload.source_sha -ne $Plan.old_source_sha -or $NewPayload.source_sha -ne $Plan.new_source_sha -or $OldPayload.schema_version -ne 'tester-lab-payload/v1' -or $NewPayload.schema_version -ne 'tester-lab-payload/v1') {throw 'Wrong source payload identity.'}
if ($NewPayload.tree_sha -notmatch '^[0-9a-f]{40}$' -or $NewPayload.repository -ne 'YehorBabenko88/tester') {throw 'Wrong new source repository/tree identity.'}
if ($OldNative.schema_version -ne 'tester-lab-service-runtime/v1' -or $NewNative.schema_version -ne 'tester-lab-service-runtime/v1') {throw 'Unknown native runtime schema.'}
Assert-UpgradeTree $OldRelease $OldPayload.files
Assert-UpgradeTree $NewSource $NewPayload.files
$Runtime=Join-Path $Root 'dependencies\service'
Assert-UpgradeTree $Runtime $OldNative.files
Assert-UpgradeTree $NewRuntime $NewNative.files
Assert-UpgradeNativeDiff $OldNative.files $NewNative.files
foreach ($Name in @('requirements-windows-lab.lock')+@($OldPayload.files.PSObject.Properties.Name | Where-Object {$_ -match '^src/(api|storage)/migrations/'})) {
    if ($OldPayload.files.PSObject.Properties[$Name].Value -cne $NewPayload.files.PSObject.Properties[$Name].Value) {throw 'Dependency/schema changes require a separate migration proposal.'}
}
if (@($NewPayload.files.PSObject.Properties.Name|Where-Object {$_ -match '^src/(api|storage)/migrations/'}).Count -ne @($OldPayload.files.PSObject.Properties.Name|Where-Object {$_ -match '^src/(api|storage)/migrations/'}).Count) {throw 'New migrations are outside app-only upgrade.'}
foreach ($Mapping in @(@('lab_service_bootstrap.py','src/api/lab_service_bootstrap.py'),@('Lib/sitecustomize.py','src/api/lab_sitecustomize.py'))) {
    if ($NewNative.files.PSObject.Properties[$Mapping[0]].Value -cne $NewPayload.files.PSObject.Properties[$Mapping[1]].Value) {throw 'Native hook does not match published source.'}
}
$Site=Join-Path $Root 'dependencies\venv\Lib\site-packages'
$PthExpected=(@($Runtime,$NewRelease,$Site,(Join-Path $Site 'win32'),(Join-Path $Site 'win32\lib')) -join "`n")+"`n"
if ([IO.File]::ReadAllText((Join-Path $NewRuntime 'Lib\site-packages\tester-lab.pth')) -cne $PthExpected) {throw 'Native path bootstrap does not pin the new release.'}
$App=Assert-UpgradeOwnedServices @($Plan.old_source_sha)
if ($App.State -ne 'Running') {throw 'Upgrade expects the current owned app running.'}
$PGPid=[int](Get-CimInstance Win32_Service -Filter "Name='$($Owner.services.postgres)'").ProcessId
$PGIdentity=Get-Content -LiteralPath (Join-Path $Root 'state\postgres-identity.json') -Raw|ConvertFrom-Json
if ($PGIdentity.installation_id -ne $Id -or [string]$PGIdentity.system_identifier -cne [string]$Plan.postgres_system_identifier) {throw 'Approved dedicated PG cluster identity differs.'}
& (Join-Path $Root 'dependencies\venv\Scripts\python.exe') -I -B -c 'import sys;sys.path.insert(0,sys.argv[1]);from src.api.lab_scm import preflight;preflight(sys.argv[2])' $OldRelease $Root
if ($LASTEXITCODE -ne 0) {throw 'Verified old release PostgreSQL/native preflight failed.'}
$Base='Registry::HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\'+$Owner.services.app
$OldClass=(Get-Item -LiteralPath ($Base+'\PythonClass')).GetValue('')
$OldPythonPath=(Get-Item -LiteralPath ($Base+'\PythonPath')).GetValue('')
$Class=Join-Path $Runtime 'lab_service_bootstrap.LabService'
if ($OldClass -cne $Class) {throw 'Unexpected native PythonClass.'}
. (Join-Path $NewSource 'scripts\windows\Repair-TesterLabPortableAcl.ps1') -FunctionsOnly
$Creator=(Get-Acl -LiteralPath $Root).GetOwner([Security.Principal.SecurityIdentifier]).Value
$Token=[Guid]::NewGuid().ToString('N')
$Parent=[IO.Path]::GetDirectoryName($NewSource)
$SourceCandidate=Join-Path $Parent ('.tester-upgrade-source-'+$Token)
$NativeCandidate=Join-Path $Parent ('.tester-upgrade-runtime-'+$Token)
if (Test-Path -LiteralPath $NewRelease) {throw 'Never adopt/overwrite an existing target SHA directory.'}
Copy-UpgradeCandidate $NewSource $SourceCandidate $NewPayload.files
Copy-UpgradeCandidate $NewRuntime $NativeCandidate $NewNative.files
foreach ($Candidate in @($SourceCandidate,$NativeCandidate)) {
    foreach ($Item in @((Get-Item -LiteralPath $Candidate))+@(Get-ChildItem -LiteralPath $Candidate -Recurse -Force)) {Set-TesterLabExplicitAcl -ItemPath $Item.FullName -CreatorSid $Creator -CreatorRights ReadAndExecute -ServiceRights ReadAndExecute}
}
$Bundle=Join-Path $Root ('backups\upgrade-'+$Token)
Assert-UpgradeBackupDestination (Join-Path $Root 'backups') $Bundle
$null=New-Item -ItemType Directory -Path $Bundle
Assert-UpgradeBackupDestination (Join-Path $Root 'backups') $Bundle
Set-TesterLabExplicitAcl -ItemPath $Bundle -CreatorSid $Creator -CreatorRights Modify
$Metadata=@{marker=$MarkerPath;config=(Join-Path $Root 'config.json');payload=$PayloadPath;native=$RuntimeManifest}
foreach ($Key in $Metadata.Keys) {Assert-UpgradeBackupDestination (Join-Path $Root 'backups') $Bundle;Assert-UpgradePlain $Metadata[$Key];[IO.File]::Copy($Metadata[$Key],(Join-Path $Bundle ($Key+'.json')),$false)}
$OldRuntime=Join-Path $Bundle 'old-native-runtime'
$Transaction=[ordered]@{schema='tester-lab-upgrade-rollback/v1';installation_id=$Id;upgrade_manifest_sha256=$UpgradeManifestSHA256;old_source_sha=$Plan.old_source_sha;new_source_sha=$Plan.new_source_sha;old_python_class=$OldClass;old_python_path=$OldPythonPath;postgres_pid=$PGPid;source_candidate=$SourceCandidate;native_candidate=$NativeCandidate;old_native_runtime=$OldRuntime}
Assert-UpgradeBackupDestination (Join-Path $Root 'backups') $Bundle
$Journal=[IO.File]::Open((Join-Path $Bundle 'transaction.json'),[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
$JournalWriter=[IO.StreamWriter]::new($Journal,[Text.UTF8Encoding]::new($false))
try {$JournalWriter.Write(($Transaction|ConvertTo-Json -Depth 7));$JournalWriter.Flush();$Journal.Flush($true)} finally {$JournalWriter.Dispose()}
$StopRequested=$false;$OldMoved=$false;$NewMoved=$false;$Committed=$false
try {
    $null=Assert-UpgradeOwnedServices @($Plan.old_source_sha)
    $StopRequested=$true
    Invoke-UpgradeAppAction stop @($Plan.old_source_sha)
    & (Join-Path $Root 'dependencies\venv\Scripts\python.exe') -I -B (Join-Path $OldRelease 'scripts\lab_portable.py') backup-restore
    if ($LASTEXITCODE -ne 0) {throw 'Owned DB backup/restore verification failed.'}
    Assert-UpgradeTree $Runtime $OldNative.files
    Assert-UpgradeTree $SourceCandidate $NewPayload.files
    Assert-UpgradeTree $NativeCandidate $NewNative.files
    [IO.Directory]::Move($SourceCandidate,$NewRelease)
    Assert-UpgradeTree $NewRelease $NewPayload.files
    Assert-UpgradeBackupDestination (Join-Path $Root 'backups') $Bundle
    Assert-UpgradePlain $Runtime
    [IO.Directory]::Move($Runtime,$OldRuntime);$OldMoved=$true
    [IO.Directory]::Move($NativeCandidate,$Runtime);$NewMoved=$true
    $Owner.releases | Add-Member -NotePropertyName $Plan.new_source_sha -NotePropertyValue $NewPayload.files
    $Owner.active_sha=$Plan.new_source_sha
    $Config=Get-Content -LiteralPath $Metadata.config -Raw|ConvertFrom-Json
    $Config.frontend_dir=Join-Path $NewRelease 'frontend'
    Write-UpgradeAtomicBytes $Metadata.config ([Text.UTF8Encoding]::new($false).GetBytes(($Config|ConvertTo-Json -Depth 20))) $Parent
    Write-UpgradeAtomicBytes $PayloadPath ([IO.File]::ReadAllBytes($Plan.new_payload_path)) $Parent
    Write-UpgradeAtomicBytes $RuntimeManifest ([IO.File]::ReadAllBytes($Plan.new_runtime_manifest_path)) $Parent
    Write-UpgradeAtomicBytes $MarkerPath ([Text.UTF8Encoding]::new($false).GetBytes(($Owner|ConvertTo-Json -Depth 30))) $Parent
    Set-UpgradeAppRegistry $Plan.new_source_sha $Class (@($Runtime,$NewRelease,$Site,(Join-Path $Site 'win32'),(Join-Path $Site 'win32\lib')) -join ';')
    Invoke-UpgradeAppAction start @($Plan.new_source_sha)
    Assert-UpgradeReady $Plan.new_source_sha $Plan.new_payload_sha256 $true
    $null=Assert-UpgradeOwnedServices @($Plan.new_source_sha)
    $Committed=$true
    Write-Output ('UPGRADE_PASS source='+$Plan.new_source_sha+' rollback_bundle='+$Bundle)
} catch {
    $Failure=$_
    if ($StopRequested) {
        $null=Assert-UpgradeOwnedServices @($Plan.old_source_sha,$Plan.new_source_sha)
        Invoke-UpgradeAppAction stop @($Plan.old_source_sha,$Plan.new_source_sha)
        Assert-UpgradeBackupDestination (Join-Path $Root 'backups') $Bundle
        if ($NewMoved) {Assert-UpgradePlain $Runtime;[IO.Directory]::Move($Runtime,(Join-Path $Bundle 'failed-new-native-runtime'))}
        if ($OldMoved) {Assert-UpgradePlain $OldRuntime;Assert-UpgradePlain ([IO.Path]::GetDirectoryName($Runtime));[IO.Directory]::Move($OldRuntime,$Runtime)}
        foreach ($Key in $Metadata.Keys) {Write-UpgradeAtomicBytes $Metadata[$Key] ([IO.File]::ReadAllBytes((Join-Path $Bundle ($Key+'.json')))) $Parent}
        $Owner=Get-Content -LiteralPath $MarkerPath -Raw|ConvertFrom-Json
        Set-UpgradeAppRegistry $Plan.old_source_sha $OldClass $OldPythonPath
        Invoke-UpgradeAppAction start @($Plan.old_source_sha)
        Assert-UpgradeReady $Plan.old_source_sha $Plan.old_payload_sha256 $false
        Write-Output ('ROLLBACK_OLD_APP_PASS retained_bundle='+$Bundle)
    }
    throw $Failure
} finally {
    $Report=[ordered]@{schema='tester-lab-upgrade-result/v1';installation_id=$Id;old_source_sha=$Plan.old_source_sha;new_source_sha=$Plan.new_source_sha;committed=$Committed;postgres_pid_unchanged=$PGPid;rollback_bundle=$Bundle;completed_utc=[DateTime]::UtcNow.ToString('o')}
    Assert-UpgradeBackupDestination (Join-Path $Root 'backups') $Bundle
    [IO.File]::WriteAllText((Join-Path $Bundle 'result.json'),($Report|ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))
}
