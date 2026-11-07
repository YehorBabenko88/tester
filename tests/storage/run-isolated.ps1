param(
    [string]$PGBin = 'C:/Program Files/PostgreSQL/18/bin',
    [Parameter(Mandatory=$true)][string]$WorkDir
)
$ErrorActionPreference = 'Stop'
$testRoot = Join-Path ([IO.Path]::GetFullPath($WorkDir)) ('pg-' + [Guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Path $testRoot | Out-Null
$dataPath = Join-Path $testRoot 'data'
$serverLog = Join-Path $testRoot 'server.log'
$testLog = Join-Path $testRoot 'tests.log'
$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$listener.Start()
$testPort = $listener.LocalEndpoint.Port
$listener.Stop()
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
$previousPath = $env:PATH
$previousDsn = $env:TESTER_TEST_ADMIN_DSN
$started = $false
$testExit = 1
try {
    $env:TEMP = $testRoot
    $env:TMP = $testRoot
    $env:PATH = $PGBin + [IO.Path]::PathSeparator + $env:PATH
    & (Join-Path $PGBin 'initdb.exe') -D $dataPath -U storage_test_admin --auth=trust --no-locale --encoding=UTF8
    if ($LASTEXITCODE -ne 0) { throw 'Synthetic initdb failed' }
    @'
host all storage_test_admin 127.0.0.1/32 trust
host all all 127.0.0.1/32 scram-sha-256
'@ | Set-Content -Encoding ascii (Join-Path $dataPath 'pg_hba.conf')
    & (Join-Path $PGBin 'pg_ctl.exe') -D $dataPath -l $serverLog -o "-h 127.0.0.1 -p $testPort -c timezone=UTC -c fsync=on -c synchronous_commit=on -c full_page_writes=on" -w start
    if ($LASTEXITCODE -ne 0) { throw 'Synthetic PostgreSQL failed to start' }
    $started = $true
    $env:TESTER_TEST_ADMIN_DSN = "host=127.0.0.1 port=$testPort user=storage_test_admin dbname=postgres connect_timeout=5"
    python -m unittest discover -v *> $testLog
    $testExit = $LASTEXITCODE
    Get-Content -LiteralPath $testLog
    Write-Output "Test artifacts: $testRoot"
} finally {
    if ($started) { & (Join-Path $PGBin 'pg_ctl.exe') -D $dataPath -m fast -w stop }
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
    $env:PATH = $previousPath
    $env:TESTER_TEST_ADMIN_DSN = $previousDsn
}
exit $testExit
