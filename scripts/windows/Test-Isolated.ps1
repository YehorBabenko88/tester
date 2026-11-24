# Execute on a disposable Windows CI/VM only. Creates a NEW cluster under .scratch.
[CmdletBinding()]
param([string]$PgBin, [int]$PgPort = 55439)
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Set-Location -LiteralPath $repo
if (-not $PgBin) {
    $PgBin = Get-ChildItem 'C:/Program Files/PostgreSQL' -Directory | Sort-Object Name -Descending |
        ForEach-Object { Join-Path $_.FullName 'bin' } | Select-Object -First 1
}
if (-not (Test-Path -LiteralPath (Join-Path $PgBin 'initdb.exe'))) { throw 'PostgreSQL binaries missing' }
$testRoot = Join-Path $repo ('.scratch/ci-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
$cluster = Join-Path $testRoot 'pgdata'
& (Join-Path $PgBin 'initdb.exe') -D $cluster -U tester_ci --auth=trust --encoding=UTF8 --locale=C
if ($LASTEXITCODE -ne 0) { throw 'initdb failed' }
& (Join-Path $PgBin 'pg_ctl.exe') -D $cluster -l (Join-Path $testRoot 'postgres.log') -o "-h 127.0.0.1 -p $PgPort" -w start
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL start failed' }
try {
    # Regression: an existing broad explicit ACE must NOT survive installation.
    $runtime = Join-Path $repo 'runtime'
    New-Item -ItemType Directory -Force -Path $runtime | Out-Null
    & icacls.exe $runtime /grant '*S-1-1-0:(OI)(CI)R' | Out-Null
    $env:TESTER_ADMIN_DSN = "postgresql://tester_ci@127.0.0.1:$PgPort/postgres"
    & (Join-Path $PSScriptRoot 'Install-Tester.ps1') -Database tester_ci -Role tester_app_ci -MinFreeBytes 0 -NoStart
    $allowed = @([Security.Principal.WindowsIdentity]::GetCurrent().User.Value, 'S-1-5-18')
    foreach ($ace in (Get-Acl -LiteralPath (Join-Path $runtime 'config.json')).Access) {
        $sid = $ace.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        if ($sid -notin $allowed) { throw 'Configuration ACL exposes secrets to another identity' }
    }
    $env:TESTER_ADMIN_DSN = $null
    $python = Join-Path $repo '.venv/Scripts/python.exe'
    & $python -m pip install -e '.[dev]' -c requirements-platform.lock
    if ($LASTEXITCODE -ne 0) { throw 'Dev dependencies failed' }
    $config = Get-Content -LiteralPath (Join-Path $repo 'runtime/config.json') -Raw | ConvertFrom-Json
    $env:TEST_DATABASE_URL = $config.database_url
    & $python scripts/acceptance.py --integration --security
    if ($LASTEXITCODE -ne 0) { throw 'Acceptance failed' }
    & (Join-Path $PSScriptRoot 'Start-Tester.ps1') -NoBrowser
    & (Join-Path $PSScriptRoot 'Stop-Tester.ps1')
} finally {
    $env:TESTER_ADMIN_DSN = $null
    $env:TEST_DATABASE_URL = $null
    & (Join-Path $PgBin 'pg_ctl.exe') -D $cluster -m fast -w stop
}
