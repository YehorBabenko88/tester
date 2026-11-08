# Proposed fixed payload only. No automatic elevation; default mode is read-only.
[CmdletBinding()]
param([switch]$RegisterAndStart)
$ErrorActionPreference='Stop'
$root='C:\ProgramData\TesterPhysicalTest20261107'
$sha='7df3ee987c2a7cefbff13db82442faf0fb09e689'
$digest='002b6becb6a8b36a6622558ff2d423f3a945b935dc1ae9f434d2a5c387716cbf'
$release=Join-Path $root ('releases\'+$sha)
$installer=Join-Path $release 'scripts\windows\Install-PhysicalTest.ps1'
if(-not $RegisterAndStart){
    & $installer -ReleaseSha $sha -ManifestSHA256 $digest -Verify
    [ordered]@{mode='PROPOSED_ONLY_NO_INSTALL';candidate=$sha;manifest_sha256=$digest;
        services=@('TesterPhysicalTest20261107','TesterPhysicalPg20261107');account='NT AUTHORITY\LocalService';
        postgres='127.0.0.1:55469';api='127.0.0.1:18765';automatic_elevation=$false;
        requires_new_human_administrator_action=$true;deletes_data=$false}|ConvertTo-Json
    return
}
# This switch is only for a new explicit human administrator action. No RunAs here.
$principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){
    throw 'New human administrator action required. No automatic elevation or service operation performed.'
}
$stage='REGISTER';$result='FAILED'
try {
    & $installer -ReleaseSha $sha -ManifestSHA256 $digest -Install
    $stage='START'
    $native=@(& (Join-Path $root 'venv\Scripts\python.exe') -I -B (Join-Path $release 'scripts\windows\physical_service.py') start --release $release --manifest-sha256 $digest 2>&1)
    if($LASTEXITCODE -ne 0){throw 'Owned service start command failed'}
    $result='REGISTERED_START_REQUESTED'
} finally {
    $data=@{result=$result;stage=$stage;release_sha=$sha;manifest_sha256=$digest;utc=[DateTime]::UtcNow.ToString('o')}
    $log=Join-Path $root ('runtime\proposed-7df-operation-'+[guid]::NewGuid().ToString('N')+'.json')
    $bytes=[Text.UTF8Encoding]::new($false).GetBytes(($data|ConvertTo-Json))
    $stream=[IO.File]::Open($log,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
}
if($result -ne 'REGISTERED_START_REQUESTED'){throw 'Proposed payload did not complete; inspect owned logs, do not retry automatically'}
