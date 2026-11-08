# Source-only proposal. Separate explicit approval must cover ONE worker termination.
# Never elevates itself, stops a service, or retries privilege escalation.
[CmdletBinding()]
param(
    [switch]$FunctionsOnly,
    [ValidatePattern('^$|^[0-9a-f]{40}$')][string]$SourceSHA='',
    [ValidatePattern('^$|^[0-9a-f]{64}$')][string]$DeployManifestSHA256='',
    [ValidatePattern('^$|^[0-9a-f]{64}$')][string]$RuntimeManifestSHA256=''
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
function Assert-RecoveryPlain([string]$Path) {
    $Item=Get-Item -LiteralPath $Path -Force
    while ($null -ne $Item) {
        if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {throw 'Reparse path refused.'}
        if ($Item -is [IO.FileInfo]) {$Item=$Item.Directory} else {$Item=$Item.Parent}
    }
}
function Assert-RecoveryHash([string]$Path,[string]$Digest) {
    Assert-RecoveryPlain $Path
    if ($Digest -notmatch '^[0-9a-fA-F]{64}$' -or (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ine $Digest) {throw 'Published hash mismatch.'}
}
function Assert-RecoveryTree([string]$Directory,$Inventory) {
    Assert-RecoveryPlain $Directory
    $Expected=@{}
    foreach ($Entry in $Inventory.PSObject.Properties) {
        if ($Entry.Name -match '(^/|\\|:|(^|/)\.\.?(/|$)|//)' -or $Entry.Value -notmatch '^[0-9a-f]{64}$') {throw 'Unsafe source inventory.'}
        $Expected[$Entry.Name]=$Entry.Value
    }
    if ($Expected.Count -eq 0) {throw 'Empty source inventory.'}
    $Items=@(Get-ChildItem -LiteralPath $Directory -Force -Recurse)
    foreach ($Item in $Items) {Assert-RecoveryPlain $Item.FullName}
    $Files=@($Items|Where-Object {-not $_.PSIsContainer})
    if ($Files.Count -ne $Expected.Count) {throw 'Source inventory count mismatch.'}
    foreach ($File in $Files) {
        $Name=$File.FullName.Substring($Directory.Length+1).Replace('\','/')
        if (-not $Expected.ContainsKey($Name)) {throw 'Unpublished source file.'}
        Assert-RecoveryHash $File.FullName $Expected[$Name]
    }
}
function Test-RecoveryModule($Process,[string]$Module) {
    $Image=[string]$Process.ExecutablePath
    return ($Process.CommandLine -ieq ($Image+' -m '+$Module) -or $Process.CommandLine -ieq ('"'+$Image+'" -m '+$Module))
}
function Assert-RecoveryWorker($Worker,$Graph,[int]$SupervisorId,[int]$ServiceId,[string]$Root) {
    $Allowed=@(($Root+'\dependencies\python\python.exe'),($Root+'\dependencies\venv\Scripts\python.exe'))
    if ($Worker.ExecutablePath -inotIn $Allowed -or -not (Test-RecoveryModule $Worker 'src.api.worker')) {throw 'Foreign worker image/command.'}
    $ById=@{}
    foreach ($Process in $Graph) {$ById[[int]$Process.ProcessId]=$Process}
    $Current=$Worker;$Seen=@{};$FoundSupervisor=$false;$SupervisorPhase=$false
    while ([int]$Current.ProcessId -ne $ServiceId) {
        $Number=[int]$Current.ProcessId
        if ($Seen.ContainsKey($Number) -or $Current.ExecutablePath -inotIn $Allowed) {throw 'Foreign or cyclic process ancestry.'}
        $Seen[$Number]=$true
        if (Test-RecoveryModule $Current 'src.api.supervisor') {$SupervisorPhase=$true}
        elseif ($SupervisorPhase -or -not (Test-RecoveryModule $Current 'src.api.worker')) {throw 'Unexpected descendant module.'}
        if ($Number -eq $SupervisorId) {
            if (-not (Test-RecoveryModule $Current 'src.api.supervisor')) {throw 'Recorded supervisor is foreign.'}
            $FoundSupervisor=$true
        }
        $ParentNumber=[int]$Current.ParentProcessId
        if (-not $ById.ContainsKey($ParentNumber)) {throw 'Worker does not descend from owned service.'}
        $Parent=$ById[$ParentNumber]
        if ($null -eq $Current.CreationDate -or $null -eq $Parent.CreationDate -or $Parent.CreationDate -gt $Current.CreationDate) {throw 'Ancestry PID reuse detected.'}
        $Current=$Parent
    }
    if (-not $FoundSupervisor -or $Current.ExecutablePath -ine ($Root+'\dependencies\service\pythonservice.exe')) {throw 'Wrong service ancestor.'}
    return $Worker
}
function Initialize-RecoveryNative {
    Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class TesterRecoveryNative {
 [DllImport("kernel32.dll",SetLastError=true)] public static extern IntPtr OpenProcess(uint access,bool inherit,uint pid);
 [DllImport("kernel32.dll",SetLastError=true,CharSet=CharSet.Unicode)] public static extern bool QueryFullProcessImageName(IntPtr h,uint flags,StringBuilder value,ref uint size);
 [DllImport("kernel32.dll",SetLastError=true)] public static extern bool GetProcessTimes(IntPtr h,out long creation,out long exit,out long kernel,out long user);
 [DllImport("kernel32.dll",SetLastError=true)] public static extern bool TerminateProcess(IntPtr h,uint code);
 [DllImport("kernel32.dll",SetLastError=true)] public static extern bool CloseHandle(IntPtr h);
}
'@
}
function Open-RecoveryProcess([int]$ProcessId) {
    # PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE; no broad process access.
    $Handle=[TesterRecoveryNative]::OpenProcess(0x1001,$false,[uint32]$ProcessId)
    if ($Handle -eq [IntPtr]::Zero) {throw 'Cannot hold worker process handle.'}
    return $Handle
}
function Read-RecoveryHandle([IntPtr]$Handle) {
    $Buffer=[Text.StringBuilder]::new(32768);[uint32]$Size=32768
    [long]$Created=0;[long]$Exited=0;[long]$Kernel=0;[long]$User=0
    if (-not [TesterRecoveryNative]::QueryFullProcessImageName($Handle,0,$Buffer,[ref]$Size) -or -not [TesterRecoveryNative]::GetProcessTimes($Handle,[ref]$Created,[ref]$Exited,[ref]$Kernel,[ref]$User) -or $Exited -ne 0) {throw 'Worker handle identity unavailable.'}
    return [pscustomobject]@{Image=$Buffer.ToString();CreatedFileTime=$Created}
}
function Stop-RecoveryHandle([IntPtr]$Handle) {
    if (-not [TesterRecoveryNative]::TerminateProcess($Handle,99)) {throw 'One approved worker termination failed.'}
}
function Close-RecoveryProcess([IntPtr]$Handle) {
    if (-not [TesterRecoveryNative]::CloseHandle($Handle)) {throw 'Worker handle close failed.'}
}
function Invoke-RecoveryTermination($Candidate) {
    $Handle=Open-RecoveryProcess ([int]$Candidate.ProcessId)
    try {
        $Actual=Read-RecoveryHandle $Handle
        # CIM timestamps have microsecond resolution; native FILETIME is 100ns.
        $ExpectedTime=$Candidate.CreationDate.ToUniversalTime().ToFileTimeUtc()
        if ($Actual.Image -ine $Candidate.ExecutablePath -or [Math]::Floor([decimal]$Actual.CreatedFileTime/10) -ne [Math]::Floor([decimal]$ExpectedTime/10)) {throw 'Held worker image/creation differs; PID reuse refused.'}
        Stop-RecoveryHandle $Handle
    } finally {Close-RecoveryProcess $Handle}
}
function Get-RecoveryOtherPG {
    return (Get-CimInstance Win32_Service -Filter "Name='postgresql-x64-18'" | Select-Object Name,State,ProcessId,PathName | ConvertTo-Json -Compress)
}
function Assert-RecoveryReady {
    $NativePath=Join-Path $script:Root 'runtime\service.json'
    Assert-RecoveryPlain $NativePath
    $Native=Get-Content -LiteralPath $NativePath -Raw|ConvertFrom-Json
    $App=Get-CimInstance Win32_Service -Filter "Name='$($script:AppName)'"
    $PG=Get-CimInstance Win32_Service -Filter "Name='$($script:PGName)'"
    $PGImage='"C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe" runservice -N '+$script:PGName+' -D C:\TesterLab\postgres-data -w'
    if ($null -eq $App -or $null -eq $PG -or $App.State -ne 'Running' -or $PG.State -ne 'Running' -or $App.StartName -ine 'NT AUTHORITY\LocalService' -or $PG.StartName -ine 'NT AUTHORITY\LocalService' -or $App.PathName -cne ($script:Root+'\dependencies\service\pythonservice.exe') -or $PG.PathName -cne $PGImage) {throw 'Owned services unavailable or foreign.'}
    foreach ($Name in @($script:AppName,$script:PGName)) {
        $Reg=Get-ItemProperty -LiteralPath ('Registry::HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\'+$Name+'\Parameters')
        if ($Reg.Root -cne $script:Root -or $Reg.InstallationId -ne $script:Id -or ($Name -eq $script:AppName -and $Reg.SourceSHA -ne $SourceSHA)) {throw 'Registry ownership differs.'}
    }
    $Identity=(Invoke-RestMethod -Uri 'http://127.0.0.1:18766/api/lab/identity' -TimeoutSec 5).data
    $Health=(Invoke-RestMethod -Uri 'http://127.0.0.1:18766/api/health' -TimeoutSec 5).data
    if ($Identity.source_sha -ne $SourceSHA -or $Identity.tree_sha -ne $script:Payload.tree_sha -or $Identity.deploy_manifest_sha256 -ne $DeployManifestSHA256 -or $Identity.installation_id -ne $script:Id -or $Identity.services.app -ne $script:AppName -or $Identity.services.postgres -ne $script:PGName -or $Identity.instance_id -ne $Native.instance_id -or $Health.instance_id -ne $Native.instance_id -or $Health.status -ne 'ok' -or $Native.source_sha -ne $SourceSHA -or $Native.installation_id -ne $script:Id -or $Native.service -ne $script:AppName) {throw 'API/native published identity differs.'}
    if ($script:Instance -and ($Native.instance_id -ne $script:Instance -or [int]$App.ProcessId -ne $script:AppProcessId -or [int]$PG.ProcessId -ne $script:PGProcessId)) {throw 'Service/PG instance unexpectedly changed.'}
    if ((Get-RecoveryOtherPG) -cne $script:OtherPGBefore) {throw 'Existing PostgreSQL5432 observation changed.'}
    return [pscustomobject]@{Native=$Native;App=$App;PG=$PG}
}
function Get-RecoveryWorker($Ready) {
    $Graph=@(Get-CimInstance Win32_Process)
    $Candidates=@()
    foreach ($Process in $Graph) {
        if (-not (Test-RecoveryModule $Process 'src.api.worker')) {continue}
        try {$Candidate=Assert-RecoveryWorker $Process $Graph ([int]$Ready.Native.supervisor_pid) ([int]$Ready.App.ProcessId) $script:Root} catch {continue}
        $Children=@($Graph|Where-Object {[int]$_.ParentProcessId -eq [int]$Candidate.ProcessId -and (Test-RecoveryModule $_ 'src.api.worker')})
        if ($Children.Count -eq 0) {$Candidates+=@($Candidate)}
    }
    if ($Candidates.Count -ne 1) {throw 'Exactly one owned leaf worker required.'}
    return $Candidates[0]
}
function Get-RecoveryJob([string]$JobId) {
    if ($JobId -notmatch '^[0-9a-fA-F-]{36}$') {throw 'Invalid owned job ID.'}
    return (Invoke-RestMethod -Uri ('http://127.0.0.1:18766/api/downloads/'+$JobId) -TimeoutSec 5).data
}
function Assert-RecoveryTelemetry($Job) {
    $Telemetry=$Job.checkpoint.telemetry
    if ($Job.state -ne 'completed' -or $Telemetry.schema_version -ne 1 -or ($Telemetry.work_complete -isnot [bool]) -or -not $Telemetry.work_complete -or $Telemetry.records_ingested -ne 1440 -or $Telemetry.records_verified -ne 1440 -or $Telemetry.bytes_received -le 0 -or $Telemetry.source -ne 'bybit') {throw 'Completed job record reread/telemetry evidence missing.'}
}
function Get-RecoveryRows {
    # Only after full pinned source/native tree verification. Never prints the DSN.
    $Probe=@'
import hashlib,json,sys
from pathlib import Path
import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb
root=Path(sys.argv[1]);start=int(sys.argv[2]);end=int(sys.argv[3])
try:
    config=json.loads((root/"config.json").read_text(encoding="utf-8"))
    dsn=config["database_url"];parts=conninfo_to_dict(dsn)
    assert parts.get("host")=="127.0.0.1" and parts.get("port")=="55470" and parts.get("dbname")=="tester_lab"
    assert not parts.get("service") and not parts.get("hostaddr")
    request=dict(category="linear",symbol="BTCUSDT",source="bybit",kind="candles",interval="1",start_ms=start,end_ms=end)
    with psycopg.connect(dsn,autocommit=True,connect_timeout=5,options="-c default_transaction_read_only=on -c statement_timeout=10000") as conn:
        conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        where=" WHERE r.venue='bybit' AND r.category='linear' AND r.symbol='BTCUSDT' AND r.dataset='candles' AND r.interval='1' AND r.timestamp_ms >= %s AND r.timestamp_ms < %s"
        total,distinct=conn.execute("SELECT count(*),count(DISTINCT r.record_key) FROM tester_storage.raw_records r"+where,(start,end)).fetchone()
        rows=conn.execute("SELECT r.record_key,r.content_hash,r.payload,p.record FROM tester_storage.raw_records r LEFT JOIN tester_lab.projections p USING(record_key)"+where+" ORDER BY r.record_key LIMIT 1441",(start,end)).fetchall()
        jobs=conn.execute("SELECT count(*) FROM tester_platform.jobs WHERE request=%s",(Jsonb(request),)).fetchone()[0]
        conn.execute("ROLLBACK")
    payload=json.dumps(rows,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
    print(json.dumps(dict(total=total,distinct_keys=distinct,values_projection_sha256=hashlib.sha256(payload).hexdigest(),projection_rows=sum(row[3] is not None for row in rows),matching_jobs=jobs)))
except Exception as exc:
    print(json.dumps(dict(error_type=type(exc).__name__)))
    raise SystemExit(1)
'@
    # Windows PowerShell 5 strips embedded native-argument quotes: encode reviewed code.
    $Encoded=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Probe))
    $Bootstrap='import base64,sys;code=base64.b64decode(sys.argv[1]);sys.argv=sys.argv[1:];exec(code)'
    $Output=& (Join-Path $script:Root 'dependencies\service\python.exe') -I -B -c $Bootstrap $Encoded $script:Root ([string]$script:StartMs) ([string]$script:EndMs) 2>$null
    if ($LASTEXITCODE -ne 0) {throw 'Read-only scoped database verification failed.'}
    return ($Output|ConvertFrom-Json)
}
if ($FunctionsOnly) {return}
$Root='C:\TesterLab';$Id='89189096-be4c-4cb3-bf2b-07b44f25fd60'
$AppName='TesterLabApp_89189096be4c4cb3bf2b07b44f25fd60';$PGName='TesterLabPG_89189096be4c4cb3bf2b07b44f25fd60'
$Instance='';$AppProcessId=0;$PGProcessId=0;$CanReport=$false
$Report=[ordered]@{schema='tester-lab-worker-recovery/v1';status='FAILED';stage='PREFLIGHT';source_sha=$SourceSHA;installation_id=$Id;termination_count=0;recovery_job_id=$null;replay_job_id=$null;error_type=$null}
$Principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {throw 'Requires separately approved administrator action covering one owned worker termination; never self-elevates.'}
if (-not $SourceSHA -or -not $DeployManifestSHA256 -or -not $RuntimeManifestSHA256) {throw 'Explicit published source/deployment/native manifest pins required.'}
try {
    Assert-RecoveryPlain $Root
    $OtherPGBefore=Get-RecoveryOtherPG
    Assert-RecoveryPlain (Join-Path $Root '.tester-lab.json')
    Assert-RecoveryPlain (Join-Path $Root 'config.json')
    $Owner=Get-Content -LiteralPath (Join-Path $Root '.tester-lab.json') -Raw|ConvertFrom-Json
    if ($Owner.schema -ne 'tester-lab/v1' -or $Owner.installation_id -ne $Id -or $Owner.root -cne $Root -or $Owner.repository -ne 'YehorBabenko88/tester' -or $Owner.active_sha -ne $SourceSHA -or $Owner.services.app -ne $AppName -or $Owner.services.postgres -ne $PGName -or $Owner.ports.api -ne 18766 -or $Owner.ports.postgres -ne 55470) {throw 'Fixed installation ownership differs.'}
    $PayloadPath=Join-Path $Root 'state\deploy-payload.json'
    Assert-RecoveryHash $PayloadPath $DeployManifestSHA256
    $Payload=Get-Content -LiteralPath $PayloadPath -Raw|ConvertFrom-Json
    if ($Payload.schema_version -ne 'tester-lab-payload/v1' -or $Payload.source_sha -ne $SourceSHA -or $Payload.tree_sha -notmatch '^[0-9a-f]{40}$') {throw 'Published payload identity differs.'}
    $Release=Join-Path $Root ('releases\'+$SourceSHA)
    Assert-RecoveryTree $Release $Payload.files
    $OwnerFiles=$Owner.releases.PSObject.Properties[$SourceSHA].Value
    if (@($OwnerFiles.PSObject.Properties).Count -ne @($Payload.files.PSObject.Properties).Count) {throw 'Owner source inventory count differs.'}
    foreach ($Entry in $Payload.files.PSObject.Properties) {
        $Recorded=$OwnerFiles.PSObject.Properties[$Entry.Name]
        if ($null -eq $Recorded -or $Recorded.Value -cne $Entry.Value) {throw 'Owner source inventory differs.'}
    }
    Assert-RecoveryHash $PSCommandPath $Payload.files.PSObject.Properties['scripts/windows/Test-TesterLabWorkerRecovery.ps1'].Value
    $RuntimeManifest=Join-Path $Root 'state\service-runtime.json'
    Assert-RecoveryHash $RuntimeManifest $RuntimeManifestSHA256
    $NativeInventory=Get-Content -LiteralPath $RuntimeManifest -Raw|ConvertFrom-Json
    if ($NativeInventory.schema_version -ne 'tester-lab-service-runtime/v1') {throw 'Wrong private native receipt.'}
    Assert-RecoveryTree (Join-Path $Root 'dependencies\service') $NativeInventory.files
    $Ready=Assert-RecoveryReady
    $Instance=$Ready.Native.instance_id;$AppProcessId=[int]$Ready.App.ProcessId;$PGProcessId=[int]$Ready.PG.ProcessId
    Assert-RecoveryPlain (Join-Path $Root 'runtime')
    $CanReport=$true
    $Report.instance_id=$Instance
    $Report.deploy_manifest_sha256=$DeployManifestSHA256
    $Report.native_manifest_sha256=$RuntimeManifestSHA256
    $EndMs=[DateTimeOffset]::new([DateTime]::UtcNow.Date).ToUnixTimeMilliseconds();$StartMs=$EndMs-86400000
    $Report.start_ms=$StartMs;$Report.end_ms=$EndMs
    $Before=Get-RecoveryRows;$Report.before=$Before
    $Request=@{category='linear';symbol='BTCUSDT';source='bybit';kind='candles';interval='1';start_ms=$StartMs;end_ms=$EndMs}
    $Job=(Invoke-RestMethod -Uri 'http://127.0.0.1:18766/api/downloads' -Method Post -ContentType 'application/json' -Body ($Request|ConvertTo-Json -Compress) -TimeoutSec 10).data
    $Report.recovery_job_id=[string]$Job.id;$Report.stage='WAIT_RUNNING'
    $Deadline=[DateTime]::UtcNow.AddSeconds(45)
    do {
        $Job=Get-RecoveryJob $Report.recovery_job_id
        if ($Job.state -in @('completed','failed','paused')) {break}
        if ($Job.state -eq 'running') {break}
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $Deadline)
    if ($Job.state -ne 'running') {$Report.status='NOT_PROVEN';$Report.reason='Target was not running before bounded termination window.';return}
    $Ready=Assert-RecoveryReady
    $Worker=Get-RecoveryWorker $Ready
    # Recheck target immediately before the sole held-handle kill. A later race is NOT_PROVEN.
    $Job=Get-RecoveryJob $Report.recovery_job_id
    if ($Job.state -ne 'running') {$Report.status='NOT_PROVEN';$Report.reason='Target completed or changed before termination.';return}
    Initialize-RecoveryNative
    $Report.stage='TERMINATE_ONE_WORKER';$Report.worker_before=[int]$Worker.ProcessId
    Invoke-RecoveryTermination $Worker
    $Report.termination_count=1
    $Report.stage='WAIT_RECOVERY'
    $Deadline=[DateTime]::UtcNow.AddSeconds(180);$NewWorker=$null
    do {
        Start-Sleep -Milliseconds 500
        $Ready=Assert-RecoveryReady
        try {$Observed=Get-RecoveryWorker $Ready;if ([int]$Observed.ProcessId -ne [int]$Worker.ProcessId) {$NewWorker=$Observed}} catch {}
        $Job=Get-RecoveryJob $Report.recovery_job_id
        if ($Job.state -in @('completed','failed','paused')) {break}
    } while ([DateTime]::UtcNow -lt $Deadline)
    $Report.recovery_state=$Job.state;$Report.recovery_attempts=$Job.attempts
    if ($Job.state -ne 'completed' -or $Job.attempts -lt 2 -or $null -eq $NewWorker) {$Report.status='NOT_PROVEN';$Report.reason='Same job recovery, replacement worker and second lease not all proven.';return}
    Assert-RecoveryTelemetry $Job
    $Report.worker_after=[int]$NewWorker.ProcessId;$Report.recovery_telemetry=$Job.checkpoint.telemetry
    $After=Get-RecoveryRows;$Report.after_recovery=$After
    if ($After.total -ne 1440 -or $After.distinct_keys -ne 1440 -or $After.projection_rows -ne 1440 -or $After.matching_jobs -ne ($Before.matching_jobs+1)) {throw 'Recovery row uniqueness/count or job count mismatch.'}
    $Replay=(Invoke-RestMethod -Uri 'http://127.0.0.1:18766/api/downloads' -Method Post -ContentType 'application/json' -Body ($Request|ConvertTo-Json -Compress) -TimeoutSec 10).data
    $Report.replay_job_id=[string]$Replay.id
    if ($Report.replay_job_id -eq $Report.recovery_job_id) {throw 'Replay must be a new API job.'}
    $Report.stage='WAIT_REPLAY';$Deadline=[DateTime]::UtcNow.AddSeconds(180)
    do {
        Start-Sleep -Milliseconds 500
        $null=Assert-RecoveryReady
        $Replay=Get-RecoveryJob $Report.replay_job_id
        if ($Replay.state -in @('completed','failed','paused')) {break}
    } while ([DateTime]::UtcNow -lt $Deadline)
    Assert-RecoveryTelemetry $Replay
    $Report.replay_telemetry=$Replay.checkpoint.telemetry
    $Final=Get-RecoveryRows;$Report.after_replay=$Final
    if ($Final.total -ne 1440 -or $Final.distinct_keys -ne 1440 -or $Final.projection_rows -ne 1440 -or $Final.matching_jobs -ne ($Before.matching_jobs+2) -or $Final.values_projection_sha256 -cne $After.values_projection_sha256) {throw 'Replay uniqueness/value/provenance or job count mismatch.'}
    $null=Assert-RecoveryReady
    Assert-RecoveryTree $Release $Payload.files
    $Report.status='WORKER_RECOVERY_AND_REPLAY_PASS';$Report.stage='COMPLETE'
} catch {
    $Report.error_type=$_.Exception.GetType().FullName
    throw 'Scoped worker recovery failed. Inspect redacted durable evidence; no automatic privilege retry.'
} finally {
    if ($CanReport) {
        $Report.completed_at=[DateTime]::UtcNow.ToString('o')
        $Report.existing_pg5432_unchanged=((Get-RecoveryOtherPG) -ceq $OtherPGBefore)
        if (-not $Report.existing_pg5432_unchanged) {$Report.status='FAILED'}
        $Target=Join-Path $Root ('runtime\worker-recovery-'+[Guid]::NewGuid().ToString('N')+'.json')
        $Bytes=[Text.UTF8Encoding]::new($false).GetBytes(($Report|ConvertTo-Json -Depth 12))
        Assert-RecoveryPlain (Join-Path $Root 'runtime')
        $File=[IO.File]::Open($Target,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        try {$File.Write($Bytes,0,$Bytes.Length);$File.Flush($true)} finally {$File.Dispose()}
        Write-Output $Target
    }
}
