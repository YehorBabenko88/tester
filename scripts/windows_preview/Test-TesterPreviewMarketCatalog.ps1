#requires -Version 5.1
# Read-only ADMINIT Preview market catalog diagnostic; no secrets or DB credentials.
[CmdletBinding()]
param([string]$BaseUrl='http://127.0.0.1:18769',[switch]$RequestRefresh)
$ErrorActionPreference='Stop'
$BaseUrl=$BaseUrl.TrimEnd('/')
if ($BaseUrl -ne 'http://127.0.0.1:18769') { throw 'Only ADMINIT loopback Preview is supported' }
Write-Host '=== Capabilities ==='
$c=Invoke-RestMethod "$BaseUrl/api/capabilities" -TimeoutSec 15
$c.data.features | ConvertTo-Json -Depth 6
foreach($category in @('linear','inverse','spot')) {
  Write-Host "=== $category snapshot BEFORE ==="
  try {
    $r=Invoke-RestMethod "$BaseUrl/api/market-catalog/$category?limit=5" -TimeoutSec 15
    $r.data | ConvertTo-Json -Depth 6
  } catch { Write-Warning "$category query: $($_.Exception.Message)" }
}
if ($RequestRefresh) {
  foreach($category in @('linear','inverse','spot')) {
    try {
      $r=Invoke-RestMethod -Method Post "$BaseUrl/api/market-catalog/$category/refresh" -TimeoutSec 15
      Write-Host "$category refresh queued=$($r.data.queued)"
    } catch { Write-Warning "$category refresh: $($_.Exception.Message)" }
  }
  Start-Sleep -Seconds 12
  foreach($category in @('linear','inverse','spot')) {
    Write-Host "=== $category snapshot AFTER ==="
    try {
      $r=Invoke-RestMethod "$BaseUrl/api/market-catalog/$category?limit=5" -TimeoutSec 15
      $r.data | ConvertTo-Json -Depth 6
    } catch { Write-Warning "$category query: $($_.Exception.Message)" }
  }
}
Write-Host '=== Public Bybit network check (read-only) ==='
try {
  $r=Invoke-WebRequest -UseBasicParsing -Uri 'https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1' -TimeoutSec 12
  Write-Host "Bybit HTTP status=$($r.StatusCode)"
  $j=$r.Content | ConvertFrom-Json
  Write-Host "retCode=$($j.retCode) count=$(@($j.result.list).Count)"
} catch { Write-Warning "Bybit unavailable from ADMINIT: $($_.Exception.Message)" }
Write-Host 'No real historical-data provider is configured by this diagnostic.'
