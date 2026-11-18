#requires -Version 5.1
<#
Preflight for opt-in real Bybit historical candle provider.
Does not modify the catalog, PostgreSQL, users, tasks, or credentials.
Requires an operator-provisioned isolated application DB and verified TLS bundle.
#>
[CmdletBinding()]
param(
  [string]$Root = (Join-Path $env:LOCALAPPDATA 'TesterPreview-ADMINIT'),
  [int]$Port = 18769
)
$ErrorActionPreference='Stop'
$source=Join-Path $Root 'source'
$python=Join-Path $Root '.venv\Scripts\python.exe'
$runtime=Join-Path $Root 'runtime'
if (-not (Test-Path -LiteralPath $python)) { throw 'Preview Python missing' }
if (-not (Test-Path -LiteralPath (Join-Path $source 'src\integration\catalog_market.py'))) { throw 'Catalog candle provider not installed' }
Write-Host '=== Read-only provider preflight ==='
Write-Host 'Required reader: src.integration.catalog_market:create_reader'
Write-Host 'Required handler: src.integration.catalog_market:create_handler'
Write-Host 'Separate TESTER_DATABASE_URL must point to a dedicated non-shared application database.'
Write-Host 'Catalog TESTER_MARKET_CATALOG_DATABASE_URL is not a substitute for jobs/storage DB.'
Write-Host 'A worker running src.api.worker is required to execute queued downloads.'
Write-Host 'Verified TLS trust bundle and application migrations are required before enabling.'
Write-Host '=== Current capabilities ==='
try {
  $r=Invoke-RestMethod "http://127.0.0.1:$Port/api/capabilities" -TimeoutSec 10
  $r.data.features | ConvertTo-Json -Depth 6
} catch { Write-Warning "API unavailable: $($_.Exception.Message)" }
Write-Host '=== Storage readiness (no passwords printed) ==='
if (-not $env:TESTER_DATABASE_URL) {
  Write-Warning 'TESTER_DATABASE_URL absent in current process; historical downloads remain disabled.'
} else {
  Push-Location $source
  try {
    @'
from src.api.config import load_settings
from src.api.db import connect
s=load_settings()
if not s.dsn:
    raise SystemExit("APP_DB_NOT_CONFIGURED")
with connect(s.dsn) as c:
    c.execute("SELECT 1 FROM tester_platform.jobs LIMIT 0")
    c.execute("SELECT 1 FROM tester_storage.schema_migrations LIMIT 0")
print("APP_DB_PLATFORM_STORAGE_SCHEMA_READABLE")
'@ | & $python -
    if ($LASTEXITCODE -ne 0) { Write-Warning 'Application DB migration/readiness check failed.' }
  } finally { Pop-Location }
}
Write-Host 'No source or database was modified.'
