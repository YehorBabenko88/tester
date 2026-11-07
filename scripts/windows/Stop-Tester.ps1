[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$runtime = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../runtime'))
if (-not (Test-Path -LiteralPath $runtime)) { return }
New-Item -ItemType File -Force -Path (Join-Path $runtime 'stop.request') | Out-Null
for ($i=0; $i -lt 60; $i++) {
    if (-not (Test-Path -LiteralPath (Join-Path $runtime 'supervisor.json'))) { Write-Output 'Tester stopped'; return }
    Start-Sleep -Milliseconds 500
}
throw 'Shutdown timed out. Inspect logs; do not kill unrelated Python processes.'
