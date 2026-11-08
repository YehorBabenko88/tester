[CmdletBinding()]
param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$config = Join-Path $repo 'runtime/config.json'
if (-not (Test-Path -LiteralPath $config)) { throw 'Run Install-Tester.ps1 first' }
$env:TESTER_CONFIG_FILE = $config
$python = Join-Path $repo '.venv/Scripts/python.exe'
Set-Location -LiteralPath $repo
$process = Start-Process -FilePath $python -ArgumentList '-m','src.api.supervisor' -WorkingDirectory $repo -WindowStyle Hidden -PassThru
$settings = Get-Content -LiteralPath $config -Raw | ConvertFrom-Json
$url = "http://127.0.0.1:$($settings.port)"
. (Join-Path $PSScriptRoot 'Tester-Readiness.ps1')
for ($i=0; $i -lt 60; $i++) {
    if ($process.HasExited) { throw 'Supervisor stopped; inspect runtime/logs' }
    try {
        $readiness = Get-TesterOperationalReadiness -Url "$url/api/health"
        if ($readiness.operational) {
            if (-not $NoBrowser) { Start-Process $url }
            Write-Output "Tester operational at $url; health=$($readiness.health); provider=$($readiness.provider)"
            return
        }
    } catch { }
    Start-Sleep -Milliseconds 500
}
# Stop the process we just launched through its graceful control path.
New-Item -ItemType File -Force -Path (Join-Path $repo 'runtime/stop.request') | Out-Null
throw 'Health check timed out; inspect runtime/logs'
