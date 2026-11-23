#requires -Version 5.1
[CmdletBinding()]
param([string]$Root='C:\TesterLab',[switch]$NoBrowser)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$Marker=Get-Content -LiteralPath (Join-Path $Root '.tester-release.json') -Raw | ConvertFrom-Json
if ($Marker.schema -ne 'tester-release-install/v1' -or $Marker.root -ne [IO.Path]::GetFullPath($Root) -or $Marker.source_sha -notmatch '^[0-9a-f]{40}$') {throw 'Unknown installation identity.'}
$Release=Join-Path $Root ('releases\'+$Marker.source_sha)
$Python=Join-Path $Root 'dependencies\venv\Scripts\python.exe'
Push-Location -LiteralPath $Release
try {
    & $Python -B -m src.api.release_service start --root $Root
    if ($LASTEXITCODE -ne 0) {throw 'Service startup failed; inspect state and runtime logs.'}
    try {$Health=Invoke-RestMethod -Uri ('http://127.0.0.1:'+$Marker.ports.api+'/api/health') -ErrorAction Stop}
    catch {
        $Response=$_.Exception.Response
        if ($null -eq $Response -or [int]$Response.StatusCode -ne 503) {throw}
        $Reader=[IO.StreamReader]::new($Response.GetResponseStream())
        try {$Health=$Reader.ReadToEnd()|ConvertFrom-Json} finally {$Reader.Dispose()}
    }
    $Evidence=Get-Content -LiteralPath (Join-Path $Root 'runtime\service.json') -Raw | ConvertFrom-Json
    if ($Health.data.instance_id -ne $Evidence.instance_id -or $Evidence.source_sha -ne $Marker.source_sha -or $Health.data.database -ne 'ok' -or $Health.data.worker -ne 'ok' -or $Health.data.storage -ne 'ok') {throw 'Readiness identity mismatch.'}
    if (-not $NoBrowser) {Start-Process ('http://127.0.0.1:'+$Marker.ports.api)}
} finally {Pop-Location}
