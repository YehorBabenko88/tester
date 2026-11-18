#requires -Version 5.1
<#
Explicit opt-in local history activation AFTER fresh Preview v3.5 installation
and a release containing this module. Does not install PostgreSQL or create
databases. Does not modify the default 5432 cluster.
#>
[CmdletBinding()]
param([string]$Root = (Join-Path $env:LOCALAPPDATA 'TesterPreview-ADMINIT'))
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$source = Join-Path $Root 'source'
$python = Join-Path $Root '.venv\Scripts\python.exe'
$runtime = Join-Path $Root 'runtime'
$secrets = Join-Path $env:ProgramData 'TesterPreview-ADMINIT-PG\secrets'
if (-not (Test-Path -LiteralPath (Join-Path $Root '.tester-preview-owner'))) { throw 'Preview ownership marker missing' }
foreach ($path in @($python,(Join-Path $source 'src\integration\catalog_market.py'),(Join-Path $source 'src\api\history_readiness.py'),(Join-Path $source 'scripts\windows_preview\migrate_preview_history.py'))) {
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required source missing: $path" }
}
function Unprotect-Preview([string]$name) {
  $path=Join-Path $secrets $name
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Dedicated Preview secret missing: $name" }
  $cipher=(Get-Content -LiteralPath $path -Raw).Trim()
  $secure=ConvertTo-SecureString -String $cipher
  return [Net.NetworkCredential]::new('',$secure).Password
}
$catalogPassword=Unprotect-Preview 'catalog-password.dpapi'
$historyPassword=Unprotect-Preview 'history-password.dpapi'
$env:TESTER_MARKET_CATALOG_DATABASE_URL="host=127.0.0.1 hostaddr=127.0.0.1 port=55473 dbname=tester_preview_catalog user=tester_preview_app password=$catalogPassword"
$env:TESTER_DATABASE_URL="host=127.0.0.1 hostaddr=127.0.0.1 port=55473 dbname=tester_preview_history user=tester_preview_history_app password=$historyPassword"
$env:TESTER_MARKET_CATALOG_ENABLED='true'
$env:TESTER_PROVIDER_FACTORY='src.integration.catalog_market:create_reader'
$env:TESTER_HANDLER_FACTORY='src.integration.catalog_market:create_handler'
$env:TESTER_DATA_DIR=$runtime
$env:TESTER_HOST='127.0.0.1'
$env:TESTER_PORT='18769'
$env:TESTER_FRONTEND_DIR=(Join-Path $source 'frontend')
try {
  Push-Location $source
  try {
    & $python -m scripts.windows_preview.migrate_preview_history
    if ($LASTEXITCODE -ne 0) { throw 'History migration/readiness failed; no process started' }
    $status=Invoke-RestMethod 'http://127.0.0.1:18769/api/capabilities' -TimeoutSec 5 -ErrorAction SilentlyContinue
    if ($status -and $status.data.features.sources.enabled -eq $false) {
      throw 'Catalog-only API still running on 18769. Stop the owned Preview API using its original lifecycle before activation.'
    }
    if (Get-NetTCPConnection -LocalPort 18769 -State Listen -ErrorAction SilentlyContinue) {
      throw 'API port occupied; refusing to start another Preview API'
    }
    if (Test-Path (Join-Path $runtime 'stop.request')) {
      throw 'stop.request exists; clear it through the owned lifecycle before activation'
    }
    $api=Start-Process -FilePath $python -ArgumentList '-m','src.api.main' -WorkingDirectory $source -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtime 'history-api.stdout.log') -RedirectStandardError (Join-Path $runtime 'history-api.stderr.log')
    Set-Content -LiteralPath (Join-Path $runtime 'preview.pid') -Value $api.Id
    $ready=$false
    for($i=0;$i -lt 40;$i++) {
      Start-Sleep -Milliseconds 500
      try {
        $cap=Invoke-RestMethod 'http://127.0.0.1:18769/api/capabilities' -TimeoutSec 2
        if ($cap.data.features.sources.enabled -eq $true) { $ready=$true; break }
      } catch {}
    }
    if (-not $ready) { throw 'History-enabled API readiness failed; inspect history-api.stderr.log' }
    $worker=Start-Process -FilePath $python -ArgumentList '-m','src.api.worker' -WorkingDirectory $source -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtime 'history-worker.stdout.log') -RedirectStandardError (Join-Path $runtime 'history-worker.stderr.log')
    Set-Content -LiteralPath (Join-Path $runtime 'history-worker.pid') -Value $worker.Id
    Write-Host "HISTORY_PREVIEW_STARTED API_PID=$($api.Id) WORKER_PID=$($worker.Id)"
  } finally { Pop-Location }
} finally {
  foreach($name in @('TESTER_MARKET_CATALOG_DATABASE_URL','TESTER_DATABASE_URL','TESTER_MARKET_CATALOG_ENABLED','TESTER_PROVIDER_FACTORY','TESTER_HANDLER_FACTORY','TESTER_DATA_DIR','TESTER_HOST','TESTER_PORT','TESTER_FRONTEND_DIR')) {
    Remove-Item "Env:$name" -ErrorAction SilentlyContinue
  }
  $catalogPassword=$null
  $historyPassword=$null
}
