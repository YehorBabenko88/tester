"""Recovery proposal helpers only; no real processes, services, HTTP or database."""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="PowerShell guard helpers")


def run_helper(tmp_path, body):
    library = Path("scripts/windows/Test-TesterLabWorkerRecovery.ps1").absolute()
    assert library.is_file(), "recovery proposal must be published"
    script = tmp_path / "guards.ps1"
    script.write_text(
        "param($Library)\n$ErrorActionPreference='Stop'\n. $Library -FunctionsOnly\nfunction Assert-RecoveryFreshCandidate {param($Candidate)}\n"
        + body,
        encoding="utf-8",
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-File", str(script), str(library)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_worker_selection_rejects_foreign_parent_image_and_pid_reuse(tmp_path):
    run_helper(
        tmp_path,
        r"""
$Root='C:\TesterLab'
$T=[DateTime]::UtcNow.AddMinutes(-1)
function Proc($Id,$Parent,$Image,$Module,$Offset) {
    [pscustomobject]@{ProcessId=$Id;ParentProcessId=$Parent;ExecutablePath=$Image;CommandLine=($Image+' -m '+$Module);CreationDate=$T.AddSeconds($Offset)}
}
$HostProcess=Proc 100 1 ($Root+'\dependencies\service\pythonservice.exe') none 0
$Supervisor=Proc 110 100 ($Root+'\dependencies\venv\Scripts\python.exe') src.api.supervisor 1
$Worker=Proc 120 110 ($Root+'\dependencies\python\python.exe') src.api.worker 2
$Graph=@($HostProcess,$Supervisor,$Worker)
$Selected=Assert-RecoveryWorker $Worker $Graph 110 100 $Root
if ($Selected.ProcessId -ne 120) {throw 'Owned worker not selected'}
foreach ($Change in @('parent','image','command','creation')) {
    $Bad=$Worker.PSObject.Copy()
    if ($Change -eq 'parent') {$Bad.ParentProcessId=999}
    if ($Change -eq 'image') {$Bad.ExecutablePath='C:\Other\python.exe'}
    if ($Change -eq 'command') {$Bad.CommandLine+=' --foreign'}
    if ($Change -eq 'creation') {$Bad.CreationDate=$T.AddSeconds(-10)}
    $Refused=$false
    try {Assert-RecoveryWorker $Bad @($HostProcess,$Supervisor,$Bad) 110 100 $Root | Out-Null} catch {$Refused=$true}
    if (-not $Refused) {throw ('Foreign worker accepted: '+$Change)}
}
$Candidate=[pscustomobject]@{ProcessId=120;ExecutablePath=$Worker.ExecutablePath;CreationDate=$Worker.CreationDate}
$script:Closed=0;$script:Killed=0
function Open-RecoveryProcess {param($ProcessId) return [IntPtr]23}
function Read-RecoveryHandle {param($Handle) [pscustomobject]@{Image=$Candidate.ExecutablePath;CreatedFileTime=$Candidate.CreationDate.AddSeconds(1).ToFileTimeUtc()}}
function Close-RecoveryProcess {param($Handle) $script:Closed++}
function Stop-RecoveryHandle {param($Handle) $script:Killed++}
$Refused=$false
try {Invoke-RecoveryTermination $Candidate} catch {$Refused=$true}
if (-not $Refused -or $script:Killed -ne 0 -or $script:Closed -ne 1) {throw 'PID reuse handle not refused/closed'}
""",
    )


def test_held_worker_handle_is_terminated_and_closed_exactly_once(tmp_path):
    run_helper(
        tmp_path,
        r"""
$Candidate=[pscustomobject]@{ProcessId=120;ExecutablePath='C:\TesterLab\dependencies\python\python.exe';CreationDate=[DateTime]::FromFileTimeUtc(133000000000000000)}
$script:Closed=0;$script:Killed=0;$script:Opened=0
function Open-RecoveryProcess {param($ProcessId) $script:Opened++;return [IntPtr]23}
function Read-RecoveryHandle {param($Handle) [pscustomobject]@{Image=$Candidate.ExecutablePath;CreatedFileTime=$Candidate.CreationDate.ToFileTimeUtc()}}
function Close-RecoveryProcess {param($Handle) $script:Closed++}
function Stop-RecoveryHandle {param($Handle) $script:Killed++}
Invoke-RecoveryTermination $Candidate
if ($script:Opened -ne 1 -or $script:Killed -ne 1 -or $script:Closed -ne 1) {throw 'Expected exactly one held handle/termination/close'}
$script:Closed=0;$script:Killed=0
function Read-RecoveryHandle {param($Handle) [pscustomobject]@{Image=$Candidate.ExecutablePath;CreatedFileTime=($Candidate.CreationDate.ToFileTimeUtc()+9)}}
Invoke-RecoveryTermination $Candidate
if ($script:Killed -ne 1 -or $script:Closed -ne 1) {throw 'CIM microsecond truncation refused'}
$script:Closed=0;$script:Killed=0
function Read-RecoveryHandle {param($Handle) [pscustomobject]@{Image=$Candidate.ExecutablePath;CreatedFileTime=($Candidate.CreationDate.ToFileTimeUtc()+10)}}
$Refused=$false
try {Invoke-RecoveryTermination $Candidate} catch {$Refused=$true}
if (-not $Refused -or $script:Killed -ne 0 -or $script:Closed -ne 1) {throw 'Different native creation microsecond accepted'}

$script:Closed=0;$script:Killed=0
function Read-RecoveryHandle {param($Handle) [pscustomobject]@{Image=$Candidate.ExecutablePath;CreatedFileTime=$Candidate.CreationDate.ToFileTimeUtc()}}
function Stop-RecoveryHandle {param($Handle) $script:Killed++;throw 'Synthetic native error'}
$Refused=$false
try {Invoke-RecoveryTermination $Candidate} catch {$Refused=$true}
if (-not $Refused -or $script:Killed -ne 1 -or $script:Closed -ne 1) {throw 'Native error leaked handle or retried kill'}

$script:Closed=0;$script:Killed=0
function Read-RecoveryHandle {param($Handle) [pscustomobject]@{Image='C:\Other\python.exe';CreatedFileTime=$Candidate.CreationDate.ToFileTimeUtc()}}
$Refused=$false
try {Invoke-RecoveryTermination $Candidate} catch {$Refused=$true}
if (-not $Refused -or $script:Killed -ne 0 -or $script:Closed -ne 1) {throw 'Foreign image handle accepted or leaked'}
""",
    )


def test_redirector_argv0_is_independent_of_actual_image_and_fresh_guard_runs(tmp_path):
    run_helper(
        tmp_path,
        r"""
$Root='C:\TesterLab';$T=[DateTime]::UtcNow.AddMinutes(-1)
$Venv=$Root+'\dependencies\venv\Scripts\python.exe';$Base=$Root+'\dependencies\python\python.exe'
function Proc($Id,$Parent,$Image,$Argv0,$Module,$Offset) {
 [pscustomobject]@{ProcessId=$Id;ParentProcessId=$Parent;ExecutablePath=$Image;CommandLine=('"'+$Argv0+'" -m '+$Module+' ');CreationDate=$T.AddSeconds($Offset)}
}
$Service=Proc 100 1 ($Root+'\dependencies\service\pythonservice.exe') unused unused 0
$SupervisorRedirector=Proc 110 100 $Venv $Venv src.api.supervisor 1
$Supervisor=Proc 111 110 $Base $Venv src.api.supervisor 2
$WorkerRedirector=Proc 120 111 $Venv $Venv src.api.worker 3
$Worker=Proc 121 120 $Base $Venv src.api.worker 4
$Graph=@($Service,$SupervisorRedirector,$Supervisor,$WorkerRedirector,$Worker)
$Selected=Assert-RecoveryWorker $Worker $Graph 110 100 $Root
if ($Selected.ProcessId -ne 121) {throw 'Actual redirected leaf rejected'}
foreach ($Kind in @('actualimage','argv0','extraarg')) {
 $Bad=$Worker.PSObject.Copy()
 if ($Kind -eq 'actualimage') {$Bad.ExecutablePath='C:\Other\python.exe'}
 if ($Kind -eq 'argv0') {$Bad.CommandLine='C:\Other\python.exe -m src.api.worker'}
 if ($Kind -eq 'extraarg') {$Bad.CommandLine+=' --foreign'}
 $Refused=$false
 try {Assert-RecoveryWorker $Bad @($Service,$SupervisorRedirector,$Supervisor,$WorkerRedirector,$Bad) 110 100 $Root|Out-Null} catch {$Refused=$true}
 if (-not $Refused) {throw ('Redirector scope accepted '+$Kind)}
}
$script:Closed=0;$script:Killed=0;$script:Fresh=0
function Open-RecoveryProcess {param($ProcessId) [IntPtr]23}
function Read-RecoveryHandle {param($Handle) [pscustomobject]@{Image=$Worker.ExecutablePath;CreatedFileTime=$Worker.CreationDate.ToFileTimeUtc()}}
function Close-RecoveryProcess {param($Handle) $script:Closed++}
function Stop-RecoveryHandle {param($Handle) $script:Killed++}
function Assert-RecoveryFreshCandidate {param($Candidate) $script:Fresh++;throw 'Synthetic fresh graph changed'}
$Refused=$false
try {Invoke-RecoveryTermination $Worker} catch {$Refused=$true}
if (-not $Refused -or $script:Fresh -ne 1 -or $script:Killed -ne 0 -or $script:Closed -ne 1) {throw 'Fresh graph not rechecked with held handle'}
""",
    )


def test_external_tool_manifest_pins_unchanged_app_and_exact_published_script(tmp_path):
    run_helper(
        tmp_path,
        r"""
$Published=Join-Path $PSScriptRoot 'published';$ToolSHA='a'*40;$AppSHA='b'*40;$Deploy='c'*64;$Native='d'*64
$Directory=Join-Path (Join-Path $Published $ToolSHA) 'scripts\windows'
$null=New-Item -ItemType Directory -Path $Directory -Force
$ToolPath=Join-Path $Directory 'Test-TesterLabWorkerRecovery.ps1'
Copy-Item -LiteralPath $Library -Destination $ToolPath
$Digest=(Get-FileHash -LiteralPath $ToolPath -Algorithm SHA256).Hash.ToLowerInvariant()
$Manifest=Join-Path $PSScriptRoot 'tool.json'
$Receipt=[ordered]@{schema='tester-lab-recovery-tool/v1';application_source_sha=$AppSHA;deploy_manifest_sha256=$Deploy;runtime_manifest_sha256=$Native;tool_source_sha=$ToolSHA;script_sha256=$Digest;published_script_path=$ToolPath}
$Receipt|ConvertTo-Json|Set-Content -LiteralPath $Manifest -Encoding UTF8
$ManifestHash=(Get-FileHash -LiteralPath $Manifest -Algorithm SHA256).Hash.ToLowerInvariant()
$null=Assert-RecoveryTool $Manifest $ManifestHash $AppSHA $Deploy $Native $ToolPath $Published
foreach ($Kind in @('app','deploy','native','path','manifesthash')) {
 $ExpectedApp=$AppSHA;$ExpectedDeploy=$Deploy;$ExpectedNative=$Native;$Script=$ToolPath;$Pin=$ManifestHash
 if ($Kind -eq 'app') {$ExpectedApp='e'*40}
 if ($Kind -eq 'deploy') {$ExpectedDeploy='e'*64}
 if ($Kind -eq 'native') {$ExpectedNative='e'*64}
 if ($Kind -eq 'path') {$Script=$Library}
 if ($Kind -eq 'manifesthash') {$Pin='e'*64}
 $Refused=$false
 try {Assert-RecoveryTool $Manifest $Pin $ExpectedApp $ExpectedDeploy $ExpectedNative $Script $Published|Out-Null} catch {$Refused=$true}
 if (-not $Refused) {throw ('External manifest bypass '+$Kind)}
}
Add-Content -LiteralPath $ToolPath '# tampered tool'
$Refused=$false
try {Assert-RecoveryTool $Manifest $ManifestHash $AppSHA $Deploy $Native $ToolPath $Published|Out-Null} catch {$Refused=$true}
if (-not $Refused) {throw 'Modified published tool accepted'}
""",
    )
