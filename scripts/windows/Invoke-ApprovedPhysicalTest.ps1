throw 'RETIRED candidate0ff59c69: native JobObject defect. No service action. Follow current checkpoint for a rebuilt reviewed artifact; do not retry elevation automatically.'
# One bounded, fixed-payload UAC operation. No persistent elevated agent.
$ErrorActionPreference='Stop'
$root='C:\ProgramData\TesterPhysicalTest20261107'
$sha='0ff59c69b71b61f32f05eda5d6c6f1bd81490c8d'
$digest='ec9ce08fa385588db1d7cd3e95c45dfd518a9ef04b694691f8b5dece700656ec'
$release=Join-Path $root ('releases\'+$sha)
$stage='REGISTER'
$result='FAILED'
try {
    & (Join-Path $release 'scripts\windows\Install-PhysicalTest.ps1') -ReleaseSha $sha -ManifestSHA256 $digest -Install
    $stage='START'
    $native=@(& (Join-Path $root 'venv\Scripts\python.exe') -I -B (Join-Path $release 'scripts\windows\physical_service.py') start --release $release --manifest-sha256 $digest 2>&1)
    if($LASTEXITCODE -ne 0){throw 'Owned service start command failed'}
    $result='REGISTERED_START_REQUESTED'
} catch {
    # Native diagnostic strings can contain private configuration; fixed codes only.
    $result='FAILED'
} finally {
    $log=Join-Path $root ('runtime\uac-operation-'+[guid]::NewGuid().ToString('N')+'.json')
    $data=@{result=$result;stage=$stage;release_sha=$sha;manifest_sha256=$digest;utc=[DateTime]::UtcNow.ToString('o')}
    $bytes=[Text.UTF8Encoding]::new($false).GetBytes(($data|ConvertTo-Json))
    $stream=[IO.File]::Open($log,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
}
if($result -eq 'FAILED'){exit 1}
exit 0
