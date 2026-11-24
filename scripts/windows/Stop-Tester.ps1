[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$runtime = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../runtime'))
if (-not (Test-Path -LiteralPath $runtime)) { return }
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$config = Join-Path $runtime 'config.json'
try {
    if (Test-Path -LiteralPath $config) {
        $env:TESTER_CONFIG_FILE = $config
        Push-Location -LiteralPath $repo
        try { & (Join-Path $repo '.venv/Scripts/python.exe') -m src.api.control drain }
        finally { Pop-Location }
        if ($LASTEXITCODE -ne 0) { Write-Warning 'Database drain unavailable; requesting process stop anyway' }
    }
} finally {
    New-Item -ItemType File -Force -Path (Join-Path $runtime 'stop.request') | Out-Null
}
for ($i=0; $i -lt 60; $i++) {
    if (-not (Test-Path -LiteralPath (Join-Path $runtime 'supervisor.json'))) { Write-Output 'Tester stopped'; return }
    Start-Sleep -Milliseconds 500
}
throw 'Shutdown timed out. Inspect logs; do not kill unrelated Python processes.'
