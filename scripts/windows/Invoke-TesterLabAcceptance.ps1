# One bounded administrator action, only after explicit user approval. No RunAs here.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-fA-F]{64}$')][string]$ManifestSHA256)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$LabRoot='C:\TesterLab'
$Manager=Join-Path $PSScriptRoot 'Manage-TesterLab.ps1'
$Report=[ordered]@{kind='SCM_ACCEPTANCE';status='FAILED';stage='PREFLIGHT';source_sha=$null;checks=@();error_type=$null}
$Before=Get-CimInstance Win32_Service -Filter "Name='postgresql-x64-18'" | Select-Object Name,State,ProcessId,PathName
function Invoke-Action([string]$Action) {
    & $Manager -Action $Action -ManifestSHA256 $ManifestSHA256
    if ($LASTEXITCODE -ne 0) { throw 'Scoped native service action failed.' }
}
function Assert-Ready {
    $Owner=Get-Content -LiteralPath (Join-Path $LabRoot '.tester-lab.json') -Raw | ConvertFrom-Json
    $Native=Get-Content -LiteralPath (Join-Path $LabRoot 'runtime\service.json') -Raw | ConvertFrom-Json
    $Health=Invoke-RestMethod -Uri 'http://127.0.0.1:18766/api/health' -TimeoutSec 5
    if ($Health.data.status -ne 'ok' -or $Health.data.instance_id -ne $Native.instance_id -or $Native.source_sha -ne $Owner.active_sha) {
        throw 'Native instance/readiness/source mismatch.'
    }
    foreach ($Name in @($Owner.services.app,$Owner.services.postgres)) {
        $Service=Get-CimInstance Win32_Service -Filter "Name='$Name'"
        if ($Service.State -ne 'Running' -or $Service.StartName -ne 'NT AUTHORITY\LocalService' -or $Service.ProcessId -eq 0) {
            throw 'Owned native service not operational as LocalService.'
        }
    }
    $Report.source_sha=$Owner.active_sha
    $Report.checks+=@{source_sha=$Native.source_sha;instance_id=$Native.instance_id;supervisor_pid=$Native.supervisor_pid;health='ok'}
}
try {
    $Report.stage='INSTALL'
    Invoke-Action 'install'
    $Report.stage='FIRST_START'
    Invoke-Action 'start'
    Assert-Ready
    $Report.stage='STOP'
    Invoke-Action 'stop'
    $Owner=Get-Content -LiteralPath (Join-Path $LabRoot '.tester-lab.json') -Raw | ConvertFrom-Json
    foreach ($Name in @($Owner.services.app,$Owner.services.postgres)) {
        if ((Get-Service -Name $Name).Status -ne 'Stopped') { throw 'Owned service did not stop.' }
    }
    $Report.stage='SECOND_START'
    Invoke-Action 'start'
    Assert-Ready
    $After=Get-CimInstance Win32_Service -Filter "Name='postgresql-x64-18'" | Select-Object Name,State,ProcessId,PathName
    if (($Before | ConvertTo-Json -Compress) -cne ($After | ConvertTo-Json -Compress)) { throw 'Existing PostgreSQL5432 service observation changed.' }
    $Report.stage='COMPLETE'
    $Report.status='NATIVE_START_STOP_START_PASS'
} catch {
    $Report.error_type=$_.Exception.GetType().FullName
    throw
} finally {
    $Report.completed_at=[DateTime]::UtcNow.ToString('o')
    $Target=Join-Path $LabRoot ('runtime\scm-acceptance-'+[Guid]::NewGuid().ToString('N')+'.json')
    $Bytes=[Text.UTF8Encoding]::new($false).GetBytes(($Report | ConvertTo-Json -Depth 8))
    $File=[IO.File]::Open($Target,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try { $File.Write($Bytes,0,$Bytes.Length); $File.Flush($true) } finally { $File.Dispose() }
}
