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
        "param($Library)\n$ErrorActionPreference='Stop'\n. $Library -FunctionsOnly\n" + body,
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
