#requires -Version 5.1
#requires -RunAsAdministrator
<#
Tester Preview v3.5: isolated, disposable PostgreSQL 18 catalog and desktop shortcuts.
Run from an elevated PowerShell session. No existing PostgreSQL service/5432 is modified.
Unverified on ADMINIT: review before running. Does not import real market history.
#>
[CmdletBinding()]
param([switch]$Reinstall,[switch]$CleanupOnly)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$commit = 'dbf7316fd0f4b891f2d997b17ea26b71d5776500'
$root = Join-Path $env:LOCALAPPDATA 'TesterPreview-ADMINIT'
$pgRoot = Join-Path $env:ProgramData 'TesterPreview-ADMINIT-PG'
$backup = $null
$pgbin = 'C:\Program Files\PostgreSQL\18\bin'
$port = 55473
$webPort = 18769
$taskName = 'TesterPreviewPg-ADMINIT'
$account = 'TesterPgPreview'
$desktop = [Environment]::GetFolderPath('Desktop')
function Assert-External([string]$exe) { if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw "Missing required executable: $exe" } }
function Invoke-Checked([string]$exe,[string[]]$argv) { & $exe @argv; if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $exe" } }
function Secret([int]$bytes=32) {
  $b=New-Object byte[] $bytes
  [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
  $s=[Convert]::ToBase64String($b).TrimEnd('=').Replace('+','A').Replace('/','B')
  [Array]::Clear($b,0,$b.Length)
  return $s
}
function Wait-Port([int]$p,[int]$seconds=40) {
  for($i=0;$i -lt $seconds*2;$i++) {
    try { $c=New-Object Net.Sockets.TcpClient('127.0.0.1',$p);$c.Dispose();return } catch { Start-Sleep -Milliseconds 500 }
  }
  throw "PostgreSQL did not start on $p"
}
function Protect-String([string]$value,[string]$path) {
  $cipher=ConvertFrom-SecureString -SecureString (ConvertTo-SecureString -String $value -AsPlainText -Force)
  [IO.File]::WriteAllText($path,$cipher,[Text.Encoding]::ASCII)
}
function Is-Preview-Process([int]$processId) {
  $p=Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
  if (-not $p) { return $false }
  return $p.ExecutablePath -and $p.ExecutablePath.StartsWith((Join-Path $root '.venv'),[StringComparison]::OrdinalIgnoreCase)
}
if (-not $CleanupOnly) { foreach($name in @('initdb.exe','postgres.exe','psql.exe')) { Assert-External (Join-Path $pgbin $name) }
foreach($cmd in @('git','py')) { if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "Missing prerequisite: $cmd" } }
if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) { throw 'Windows Task Scheduler required' } }
function Assert-PreviewDirectory([string]$path) {
  if (-not (Test-Path -LiteralPath $path)) { return }
  $item=Get-Item -LiteralPath $path -Force
  if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Unsafe reparse root: $path" }
  $links=Get-ChildItem -LiteralPath $path -Force -Recurse -Attributes ReparsePoint -ErrorAction Stop
  if ($links) { throw "Unsafe reparse child in $path" }
}
function Stop-PreviewWeb {
  $pidFile=Join-Path $root 'runtime\preview.pid'
  if (-not (Test-Path -LiteralPath $pidFile)) { return }
  $oldId=0
  if (-not [int]::TryParse((Get-Content -LiteralPath $pidFile -Raw).Trim(),[ref]$oldId)) { throw 'Invalid preview.pid' }
  $proc=Get-CimInstance Win32_Process -Filter "ProcessId=$oldId" -ErrorAction SilentlyContinue
  if (-not $proc) { return }
  $venv=Join-Path $root '.venv'
  if (-not $proc.ExecutablePath -or -not $proc.ExecutablePath.StartsWith($venv+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'PID belongs to another process; refusing cleanup' }
  New-Item -ItemType File -Path (Join-Path $root 'runtime\stop.request') -Force | Out-Null
  Start-Sleep -Seconds 3
  $proc=Get-CimInstance Win32_Process -Filter "ProcessId=$oldId" -ErrorAction SilentlyContinue
  if ($proc -and $proc.ExecutablePath -and $proc.ExecutablePath.StartsWith($venv+'\',[StringComparison]::OrdinalIgnoreCase)) { Stop-Process -Id $oldId -Force }
}
function Remove-PreviewOwned([bool]$PreserveAccount=$false) {
  Assert-PreviewDirectory $root
  Assert-PreviewDirectory $pgRoot
  if ((Test-Path $pgRoot) -and -not (Test-Path (Join-Path $pgRoot '.tester-preview-pg-owner'))) { throw 'Unverified PG directory; refusing deletion' }
  $task=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
  if ($task) {
    $cmd=($task.Actions | ForEach-Object { $_.Execute+' '+$_.Arguments }) -join ' '
    if ($cmd -notmatch [regex]::Escape($pgRoot) -and $cmd -notmatch 'TesterPreview-ADMINIT') { throw 'Task name collision: refusing cleanup' }
  }
  if (Test-Path -LiteralPath $root) {
    if (-not (Test-Path -LiteralPath (Join-Path $root '.tester-preview-owner'))) {
      # v2.1 failed before writing the marker; accept only its exact pinned source and named installer artifacts.
      $gitDir=Join-Path $root 'source\.git'
      if (-not (Test-Path -LiteralPath $gitDir)) { throw 'Unknown installation directory: no owner marker or verified source' }
      $actual=(& git -C (Join-Path $root 'source') rev-parse HEAD 2>$null)
      if ($LASTEXITCODE -ne 0 -or $actual.Trim() -ne $commit -or -not (Test-Path (Join-Path $root '.venv\Scripts\python.exe'))) { throw 'Unverified partial installation; refusing deletion' }
    }
    Stop-PreviewWeb
  }
  foreach($t in @($taskName,'TesterPreviewInit-ADMINIT','TesterPreviewProbe-ADMINIT')) {
    $found=Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
    if ($found) {
      $taskAction=($found.Actions | ForEach-Object { $_.Execute+' '+$_.Arguments }) -join ' '
      if ($taskAction -notmatch [regex]::Escape($pgRoot)) { throw "Unverified scheduled task: $t" }
      Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
      Unregister-ScheduledTask -TaskName $t -Confirm:$false
    }
  }
  Start-Sleep -Seconds 3
  $listeners=Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  if ($listeners) { throw "Port $port still listening; refusing deletion" }
  $desktop=[Environment]::GetFolderPath('Desktop')
  foreach($n in @('Tester Preview - Start.lnk','Tester Preview - Uninstall.lnk')) {
    $lnk=Join-Path $desktop $n
    if (Test-Path $lnk) {
      $shortcut=(New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
      if ($shortcut.Arguments -like "*$root*") { Remove-Item -LiteralPath $lnk -Force }
    }
  }
  if (Test-Path $root) { Remove-Item -LiteralPath $root -Recurse -Force }
  if (Test-Path $pgRoot) { Remove-Item -LiteralPath $pgRoot -Recurse -Force }
  $user=Get-LocalUser -Name $account -ErrorAction SilentlyContinue
  if ($user) {
    if ($user.Description -notlike '*Tester Preview*') { throw 'Existing account not identified as Tester Preview; refusing deletion' }
    if (-not $PreserveAccount) { Remove-LocalUser -Name $account }
  }
  $parent=Split-Path -Parent $root
  $prefix=[IO.Path]::GetFileName($root)+'.backup-'
  Get-ChildItem -LiteralPath $parent -Directory -Force | Where-Object { $_.Name.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase) } | ForEach-Object {
    Assert-PreviewDirectory $_.FullName
    if (-not (Test-Path (Join-Path $_.FullName '.tester-preview-owner'))) {
      $sourceDir=Join-Path $_.FullName 'source'
      if (-not (Test-Path (Join-Path $sourceDir '.git'))) { throw "Backup ownership not verified: $($_.FullName)" }
      $actual=(& git -C $sourceDir rev-parse HEAD 2>$null)
      if ($LASTEXITCODE -ne 0 -or $actual.Trim() -ne $commit) { throw "Backup SHA mismatch: $($_.FullName)" }
    }
    Remove-Item -LiteralPath $_.FullName -Recurse -Force
  }
}
if (-not $Reinstall -and ((Test-Path $root) -or (Test-Path $pgRoot))) { throw 'Existing Preview: use -Reinstall to clean owned resources' }
if ($CleanupOnly) { Remove-PreviewOwned; Write-Host 'All verified Tester Preview resources removed'; return }
if ($Reinstall) { Remove-PreviewOwned -PreserveAccount $true }
if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { throw "Port $port occupied; refusing installation" }
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { throw 'Preview task remains; refusing installation' }
$retainedUser=Get-LocalUser -Name $account -ErrorAction SilentlyContinue
if ($retainedUser -and $retainedUser.Description -notlike '*Tester Preview*') { throw 'Unverified existing account; refusing installation' }
if ($retainedUser -and -not $Reinstall) { throw 'Preview user already exists; use -Reinstall' }
$createdUser=$false
try {
  New-Item -ItemType Directory -Path $root -Force | Out-Null
  Set-Content -LiteralPath (Join-Path $root '.tester-preview-owner') -Value "TesterPreview-ADMINIT`n$commit`n$taskName" -Encoding ASCII
  $source=Join-Path $root 'source'
  New-Item -ItemType Directory -Path $source | Out-Null
  Invoke-Checked 'git' @('-C',$source,'init','-q')
  Invoke-Checked 'git' @('-C',$source,'remote','add','origin','https://github.com/YehorBabenko88/tester.git')
  Invoke-Checked 'git' @('-C',$source,'fetch','--no-tags','--depth=1','origin',$commit)
  Invoke-Checked 'git' @('-C',$source,'checkout','--detach','-q','FETCH_HEAD')
  if ((& git -C $source rev-parse HEAD).Trim() -ne $commit) { throw 'Pinned source SHA mismatch' }
  Invoke-Checked 'py' @('-3.12','-m','venv',(Join-Path $root '.venv'))
  $python=Join-Path $root '.venv\Scripts\python.exe'
  Invoke-Checked $python @('-m','pip','install','-e',$source,'-c',(Join-Path $source 'requirements-platform.lock'))
  $runtime=Join-Path $root 'runtime'
  $cluster=Join-Path $pgRoot 'pgdata'
  $secrets=Join-Path $pgRoot 'secrets'
  foreach($d in @($runtime,$pgRoot,$cluster,$secrets)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
  Set-Content -LiteralPath (Join-Path $pgRoot '.tester-preview-pg-owner') -Value "TesterPreview-ADMINIT-PG`n$port" -Encoding ASCII
  # The service account is local, unprivileged and cannot log on interactively.
  $winPassword=Secret
  $winSecure=ConvertTo-SecureString $winPassword -AsPlainText -Force
  if ($retainedUser) {
    # Retain SID and existing batch-logon right across clean reinstall.
    # Rotate password so the new installation does not depend on old credentials.
    Set-LocalUser -Name $account -Password $winSecure -PasswordNeverExpires $true
  } else {
    New-LocalUser -Name $account -Password $winSecure -Description 'Tester Preview PostgreSQL account' -PasswordNeverExpires -UserMayNotChangePassword | Out-Null
    $createdUser=$true
  }
  $sid=(Get-LocalUser -Name $account).SID.Value
  $taskIdentity="$env:COMPUTERNAME\$account"
  # Resolve the account name before task registration. A relative '.\user'
  # is not reliably translated by Task Scheduler on localized Windows hosts.
  $resolvedSid=([Security.Principal.NTAccount]::new($taskIdentity)).Translate([Security.Principal.SecurityIdentifier]).Value
  if ($resolvedSid -ne $sid) { throw 'Task account SID translation mismatch' }
  # Refuse to run tasks unless Windows local policy grants this SID (directly
  # or through a group). No automatic broad security-policy modification.
  $policyFile=Join-Path $env:TEMP ('TesterPreview-rights-'+[guid]::NewGuid().ToString('N')+'.inf')
  try {
    & secedit.exe /export /cfg $policyFile /areas USER_RIGHTS | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect batch-logon policy' }
    $policy=Get-Content -LiteralPath $policyFile
    $allow=($policy | Where-Object { $_ -match '^SeBatchLogonRight\s*=' } | Select-Object -First 1)
    $deny=($policy | Where-Object { $_ -match '^SeDenyBatchLogonRight\s*=' } | Select-Object -First 1)
    if ($deny -and $deny -match [regex]::Escape($sid)) { throw 'Preview SID explicitly denied batch logon' }
    if (-not $allow -or $allow -notmatch [regex]::Escape($sid)) {
      # Existing local groups may grant the right; the scheduled-task probe
      # below is authoritative. Do not assume direct assignment is mandatory.
      Write-Warning 'Preview SID not directly listed in SeBatchLogonRight; task probe will verify effective right.'
    }
  } finally { Remove-Item -LiteralPath $policyFile -Force -ErrorAction SilentlyContinue }
  & icacls.exe $cluster '/inheritance:r' '/grant:r' "*$($sid):(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' "${env:USERDOMAIN}\${env:USERNAME}:(OI)(CI)F" | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'Failed to secure PG data ACL' }
  & icacls.exe $secrets '/inheritance:r' '/grant:r' "*$($sid):(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' "${env:USERDOMAIN}\${env:USERNAME}:(OI)(CI)F" | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'Failed to secure secret ACL' }
  & icacls.exe $pgRoot '/grant:r' "*$($sid):(OI)(CI)M" | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'Failed to grant PG owner write access' }
  $superPass=Secret
  $dbPass=Secret
  $historyPass=Secret
  $pwfile=Join-Path $secrets 'initdb.pw'
  [IO.File]::WriteAllText($pwfile,$superPass+[Environment]::NewLine,[Text.Encoding]::ASCII)
  $initLog=Join-Path $pgRoot 'initdb.log'
  $initPs=Join-Path $pgRoot 'Init-PrivatePg.ps1'
  @"
`$ErrorActionPreference='Stop'
& '$pgbin\initdb.exe' -D '$cluster' -U postgres -E UTF8 --auth-host=scram-sha-256 --auth-local=scram-sha-256 --pwfile='$pwfile' *> '$initLog'
if (`$LASTEXITCODE -ne 0) { exit `$LASTEXITCODE }
exit 0
"@ | Set-Content -LiteralPath $initPs -Encoding UTF8
  & icacls.exe $initPs '/grant:r' "*$($sid):R" | Out-Null
  $initTask='TesterPreviewInit-ADMINIT'
  # Check the generated account credential with a harmless one-shot probe.
  # The same account/password is subsequently used for initdb.
  $probeFile=Join-Path $pgRoot 'task-probe.txt'
  $probeTask='TesterPreviewProbe-ADMINIT'
  $probeAction=New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c whoami > "'+$probeFile+'"')
  Register-ScheduledTask -TaskName $probeTask -Action $probeAction -User $taskIdentity -Password $winPassword -Force | Out-Null
  try {
    Start-ScheduledTask -TaskName $probeTask -ErrorAction Stop
    $probeOk=$false
    for($j=0;$j -lt 40;$j++) {
      Start-Sleep -Milliseconds 500
      if (Test-Path -LiteralPath $probeFile) { $probeOk=$true;break }
    }
    if (-not $probeOk) {
      $probeInfo=Get-ScheduledTaskInfo -TaskName $probeTask
      throw "Preview account task probe did not execute; LastTaskResult=$($probeInfo.LastTaskResult). Inspect TaskScheduler/Operational"
    }
  } finally {
    if ($probeOk) { Unregister-ScheduledTask -TaskName $probeTask -Confirm:$false -ErrorAction SilentlyContinue }
  }
  $action=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy Bypass -File "'+$initPs+'"')
  Register-ScheduledTask -TaskName $initTask -Action $action -User $taskIdentity -Password $winPassword -Force | Out-Null
  $initSuccess=$false
  try {
    $baseline=Get-ScheduledTaskInfo -TaskName $initTask
    $registeredAt=Get-Date
    Start-ScheduledTask -TaskName $initTask -ErrorAction Stop
    $started=$false
    $done=$false
    for($i=0;$i -lt 240;$i++) {
      Start-Sleep -Milliseconds 500
      $state=Get-ScheduledTask -TaskName $initTask
      $info=Get-ScheduledTaskInfo -TaskName $initTask
      $result=[uint32]$info.LastTaskResult
      # 0x41303: task has not yet run; never treat this as an initdb exit code.
      if ($state.State -eq 'Running' -or $result -eq 0x41301 -or
          ($info.LastRunTime -gt $baseline.LastRunTime -and $info.LastRunTime -ge $registeredAt.AddSeconds(-3))) {
        $started=$true
      }
      if ($started -and $state.State -ne 'Running' -and $result -ne 0x41301 -and $result -ne 0x41303) {
        if ($result -ne 0) { throw "initdb task exited with $result. Inspect $initLog and TaskScheduler/Operational" }
        if (-not (Test-Path (Join-Path $cluster 'PG_VERSION'))) { throw "initdb task returned 0 but PG_VERSION is missing; inspect $initLog" }
        $done=$true
        break
      }
    }
    if (-not $done) {
      $info=Get-ScheduledTaskInfo -TaskName $initTask
      $state=Get-ScheduledTask -TaskName $initTask
      throw "initdb did not finish within 120 seconds; started=$started; state=$($state.State); lastResult=$($info.LastTaskResult). Inspect TaskScheduler/Operational"
    }
    $initSuccess=$true
  } finally {
    if ($initSuccess) {
      Unregister-ScheduledTask -TaskName $initTask -Confirm:$false -ErrorAction SilentlyContinue
      Remove-Item -LiteralPath $initPs -Force -ErrorAction SilentlyContinue
      Remove-Item -LiteralPath $pwfile -Force -ErrorAction SilentlyContinue
    } else {
      # Preserve task and initdb.log for troubleshooting; remove credential file to
      # avoid leaving the PostgreSQL superuser password in plaintext.
      Remove-Item -LiteralPath $pwfile -Force -ErrorAction SilentlyContinue
      Write-Warning "Init task retained for diagnostics: $initTask. Never restart it without provisioning a new password file."
    }
  }
  # Enforce loopback only, no default PostgreSQL port and SCRAM authentication.
  Add-Content -LiteralPath (Join-Path $cluster 'postgresql.conf') -Value "`nlisten_addresses = '127.0.0.1'`nport = $port`nlogging_collector = on`nlog_directory = 'log'`n"
  Set-Content -LiteralPath (Join-Path $cluster 'pg_hba.conf') -Encoding ASCII -Value @(
    'host all all 127.0.0.1/32 scram-sha-256',
    'host all all ::1/128 reject',
    'host all all 0.0.0.0/0 reject',
    'host all all ::0/0 reject'
  )
  $action=New-ScheduledTaskAction -Execute (Join-Path $pgbin 'postgres.exe') -Argument ('-D "'+$cluster+'"')
  Register-ScheduledTask -TaskName $taskName -Action $action -User $taskIdentity -Password $winPassword -Force | Out-Null
  Start-ScheduledTask -TaskName $taskName
  Wait-Port $port 45
  # Bootstrap dedicated non-superuser role/database. No secrets are passed in process command lines.
  $env:PGPASSWORD=$superPass
  $env:PGHOST='127.0.0.1';$env:PGPORT="$port";$env:PGUSER='postgres';$env:PGDATABASE='postgres'
  $bootstrap=Join-Path $root 'Bootstrap-Catalog.py'
  @'
import os
import psycopg
from psycopg import sql
admin = psycopg.connect(host='127.0.0.1', port=55473, dbname='postgres', user='postgres', password=os.environ['PGPASSWORD'], autocommit=True)
with admin:
    with admin.cursor() as cur:
        cur.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION').format(sql.Identifier('tester_preview_app'), sql.Literal(os.environ['TESTER_BOOTSTRAP_APP_PASSWORD'])))
        cur.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier('tester_preview_catalog'), sql.Identifier('tester_preview_app')))
        cur.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION').format(sql.Identifier('tester_preview_history_app'), sql.Literal(os.environ['TESTER_BOOTSTRAP_HISTORY_PASSWORD'])))
        cur.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier('tester_preview_history'), sql.Identifier('tester_preview_history_app')))
'@ | Set-Content -LiteralPath $bootstrap -Encoding UTF8
  try {
    $env:TESTER_BOOTSTRAP_APP_PASSWORD=$dbPass
    $env:TESTER_BOOTSTRAP_HISTORY_PASSWORD=$historyPass
    Invoke-Checked $python @($bootstrap)
  } finally {
    Remove-Item Env:TESTER_BOOTSTRAP_APP_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:TESTER_BOOTSTRAP_HISTORY_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $bootstrap -Force -ErrorAction SilentlyContinue
  }
  Protect-String $dbPass (Join-Path $secrets 'catalog-password.dpapi')
  Protect-String $historyPass (Join-Path $secrets 'history-password.dpapi')
  $env:TESTER_MARKET_CATALOG_DATABASE_URL="host=127.0.0.1 hostaddr=127.0.0.1 port=$port dbname=tester_preview_catalog user=tester_preview_app password=$dbPass"
  $env:TESTER_MARKET_CATALOG_ENABLED='true'
  try { Invoke-Checked $python @('-m','src.api.catalog_store') } finally {
    Remove-Item Env:TESTER_MARKET_CATALOG_DATABASE_URL -ErrorAction SilentlyContinue
    Remove-Item Env:TESTER_MARKET_CATALOG_ENABLED -ErrorAction SilentlyContinue
  }
  Set-Content -LiteralPath (Join-Path $root '.tester-preview-owner') -Value "TesterPreview-ADMINIT`n$commit`n$taskName" -Encoding ASCII
  Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $root 'Install-TesterPreview-ADMINIT-v3.5.ps1')
  $start=@'
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $MyInvocation.MyCommand.Path
$runtime=Join-Path $root 'runtime'
$taskName='TesterPreviewPg-ADMINIT'
if (-not (Test-Path (Join-Path $root '.tester-preview-owner'))) { throw 'Ownership marker missing' }
if (-not (Get-NetTCPConnection -State Listen -LocalPort 55473 -ErrorAction SilentlyContinue)) { Start-ScheduledTask -TaskName $taskName; Start-Sleep -Seconds 3 }
if (Get-NetTCPConnection -State Listen -LocalPort 18769 -ErrorAction SilentlyContinue) { Start-Process 'http://127.0.0.1:18769/'; return }
$cipher=(Get-Content -LiteralPath (Join-Path $env:ProgramData 'TesterPreview-ADMINIT-PG\secrets\catalog-password.dpapi') -Raw).Trim()
$secure=ConvertTo-SecureString -String $cipher
$plain=[Net.NetworkCredential]::new('',$secure).Password
$env:TESTER_HOST='127.0.0.1';$env:TESTER_PORT='18769'
$env:TESTER_DATA_DIR=$runtime
$env:TESTER_FRONTEND_DIR=Join-Path $root 'source\frontend'
$env:TESTER_MARKET_CATALOG_ENABLED='true'
$env:TESTER_MARKET_CATALOG_DATABASE_URL="host=127.0.0.1 hostaddr=127.0.0.1 port=55473 dbname=tester_preview_catalog user=tester_preview_app password=$plain"
Remove-Item Env:TESTER_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:TESTER_PROVIDER_FACTORY -ErrorAction SilentlyContinue
Remove-Item Env:TESTER_HANDLER_FACTORY -ErrorAction SilentlyContinue
Remove-Item Env:TESTER_CONFIG_FILE -ErrorAction SilentlyContinue
Remove-Item (Join-Path $runtime 'stop.request') -Force -ErrorAction SilentlyContinue
$p=Start-Process -FilePath (Join-Path $root '.venv\Scripts\python.exe') -ArgumentList '-m','src.api.main' -WorkingDirectory (Join-Path $root 'source') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtime 'stdout.log') -RedirectStandardError (Join-Path $runtime 'stderr.log')
Set-Content (Join-Path $runtime 'preview.pid') $p.Id
for($i=0;$i -lt 50;$i++) { Start-Sleep -Milliseconds 300; try { $x=Invoke-RestMethod 'http://127.0.0.1:18769/api/capabilities' -TimeoutSec 2; if($x.data.features.market_catalog.enabled -eq $true) { Start-Process 'http://127.0.0.1:18769/'; return } } catch {} }
throw 'Preview failed readiness/catalog check. Inspect runtime\stderr.log'
'@
  Set-Content -LiteralPath (Join-Path $root 'Start-TesterPreview.ps1') -Value $start -Encoding UTF8
  $uninstall=@'
$ErrorActionPreference='Stop'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$principal=[Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"'+$MyInvocation.MyCommand.Path+'"'))
  return
}
$root=Join-Path $env:LOCALAPPDATA 'TesterPreview-ADMINIT'
$pgRoot=Join-Path $env:ProgramData 'TesterPreview-ADMINIT-PG'
if (-not (Test-Path (Join-Path $root '.tester-preview-owner'))) { throw 'Preview ownership marker missing' }
if ((Read-Host 'Type DELETE TESTER PREVIEW to erase ALL owned Preview files, databases and backups') -cne 'DELETE TESTER PREVIEW') { return }
$installer=Join-Path $root 'Install-TesterPreview-ADMINIT-v3.5.ps1'
if (-not (Test-Path $installer)) { throw 'Owned installer copy missing; cannot safely clean' }
# Execute a detached copy because installation root is deleted by the cleanup.
$temp=Join-Path $env:TEMP ('TesterPreview-clean-'+[guid]::NewGuid().ToString('N')+'.ps1')
Copy-Item -LiteralPath $installer -Destination $temp
try { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $temp -CleanupOnly; if($LASTEXITCODE -ne 0) { throw 'Cleanup failed; inspect output' } }
finally { Remove-Item $temp -Force -ErrorAction SilentlyContinue }
'@
  Set-Content -LiteralPath (Join-Path $root 'Uninstall-TesterPreview.ps1') -Value $uninstall -Encoding UTF8
  $shell=New-Object -ComObject WScript.Shell
  foreach($item in @(@('Tester Preview - Start.lnk','Start-TesterPreview.ps1'),@('Tester Preview - Uninstall.lnk','Uninstall-TesterPreview.ps1'))) {
    $link=$shell.CreateShortcut((Join-Path $desktop $item[0]));$link.TargetPath='powershell.exe'
    $link.Arguments='-NoProfile -ExecutionPolicy Bypass -File "'+(Join-Path $root $item[1])+'"'
    $link.WorkingDirectory=$root;$link.Save()
  }
  Write-Host "INSTALLED. Source=$commit; PostgreSQL=127.0.0.1:$port; Preview=http://127.0.0.1:$webPort/"
  Write-Host 'Two desktop shortcuts created. Existing 5432 untouched. No real trading.'
} catch {
  Write-Warning $_.Exception.Message
  Write-Warning "Partial setup remains in $root and $pgRoot. Re-run -Reinstall to clean verified Preview-owned resources before retry."
  throw
} finally {
  foreach($name in @('PGPASSWORD','PGHOST','PGPORT','PGUSER','PGDATABASE','TESTER_BOOTSTRAP_APP_PASSWORD','TESTER_BOOTSTRAP_HISTORY_PASSWORD','TESTER_MARKET_CATALOG_DATABASE_URL','TESTER_MARKET_CATALOG_ENABLED')) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
}