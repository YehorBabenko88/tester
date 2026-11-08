# Run from an ordinary UAC-approved PowerShell. No elevation or start is performed here.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{40}$')][string]$ReleaseSha,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{64}$')][string]$ManifestSHA256,
    [switch]$Install,
    [switch]$Verify
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$root='C:\ProgramData\TesterPhysicalTest20261107'
$pgName='TesterPhysicalPg20261107'
$appName='TesterPhysicalTest20261107'
$release=Join-Path $root ('releases\'+$ReleaseSha)
$pgData=Join-Path $root 'postgres-data'
$pgCtl='C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe'
$python=Join-Path $root 'venv\Scripts\python.exe'
$entry=Join-Path $release 'scripts\windows\physical_service.py'
if($Install -and $Verify){throw 'Install and Verify are mutually exclusive'}
$plan=[ordered]@{root=$root;release=$release;manifest_sha256=$ManifestSHA256;services=@($appName,$pgName);api_port=18765;postgres_port=55469;install=[bool]$Install;verify=[bool]$Verify;starts_services=$false;deletes_data=$false}
if(-not $Install -and -not $Verify){$plan|ConvertTo-Json -Depth 4;return}

function Assert-Plain([string]$path){
    $full=[IO.Path]::GetFullPath($path)
    $cursor=$full
    while($cursor){
        if(-not(Test-Path -LiteralPath $cursor)){throw 'Required installation path missing'}
        $item=Get-Item -LiteralPath $cursor -Force
        if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Reparse path refused'}
        $cursor=[IO.Path]::GetDirectoryName($cursor)
    }
    return $full
}
function Assert-Owned([string]$path){
    $full=[IO.Path]::GetFullPath($path)
    if($full -ne $root -and -not $full.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Path outside fixed installation'}
    return Assert-Plain $full
}
function Read-BoundedJson([string]$path,[long]$bound){
    $null=Assert-Owned $path
    if((Get-Item -LiteralPath $path).Length -gt $bound){throw 'Oversized JSON refused'}
    return Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
}
function Assert-Release {
    $manifestPath=Join-Path $release 'physical-manifest.json'
    $manifest=Read-BoundedJson $manifestPath 4194304
    if((Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ManifestSHA256){throw 'Manifest digest mismatch'}
    $keys=($manifest.PSObject.Properties.Name | Sort-Object) -join ','
    if($keys -cne 'files,release_sha,schema_version' -or $manifest.schema_version -cne '1.0' -or $manifest.release_sha -cne $ReleaseSha){throw 'Invalid manifest identity'}
    $inventory=@($manifest.files.PSObject.Properties)
    if($inventory.Count -lt 1 -or $inventory.Count -gt 10000){throw 'Invalid inventory size'}
    $names=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $total=0L
    foreach($property in $inventory){
        $rel=$property.Name
        if($rel.Contains('\') -or $rel.Contains(':') -or @($rel.Split('/') | Where-Object {$_ -in @('','.','..')}).Count -gt 0 -or $property.Value -cnotmatch '^[a-f0-9]{64}$' -or -not $names.Add($rel)){throw 'Unsafe or duplicate manifest entry'}
        $path=Assert-Owned (Join-Path $release $rel)
        $file=Get-Item -LiteralPath $path -Force
        if($file.PSIsContainer -or $file.Length -gt 67108864){throw 'Invalid inventory file'}
        $total+=$file.Length
        if($total -gt 268435456){throw 'Release size limit exceeded'}
        if((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $property.Value){throw 'Release file digest mismatch'}
    }
    # Walk without following links; reject an unexpected directory link before recursion.
    $queue=[Collections.Generic.Queue[string]]::new();$queue.Enqueue((Assert-Owned $release));$found=0
    while($queue.Count){
        foreach($item in Get-ChildItem -LiteralPath $queue.Dequeue() -Force){
            $null=Assert-Owned $item.FullName
            if($item.PSIsContainer){$queue.Enqueue($item.FullName);continue}
            if($item.FullName -eq $manifestPath){continue}
            $relative=$item.FullName.Substring($release.Length+1).Replace('\','/')
            if(-not $names.Contains($relative)){throw 'Unlisted release file'}
            $found++
        }
    }
    if($found -ne $inventory.Count -or -not $names.Contains('scripts/windows/physical_service.py')){throw 'Incomplete release inventory'}
}
function Inspect-Pg {
    $service=Get-CimInstance -ClassName Win32_Service -Filter "Name='$pgName'"
    if($null -eq $service){return $null}
    # Exact executable/name/data; only pg_ctl's own runservice -w suffix is allowed.
    $pattern='^"'+[regex]::Escape($pgCtl)+'" runservice -N "'+[regex]::Escape($pgName)+'" -D "'+[regex]::Escape($pgData)+'"(?: -w)?\s*$'
    if($service.PathName -notmatch $pattern -or $service.StartName -ine 'NT AUTHORITY\LocalService' -or $service.StartMode -ne 'Manual' -or $service.State -ne 'Stopped'){throw 'Existing PostgreSQL service ownership/state not proven'}
    return $service
}

# All external checks precede any Python invocation or SCM mutation.
$logAllowed=$false
$stage='PREFLIGHT'
try {
$owner=Read-BoundedJson (Join-Path $root 'installation-owner.json') 16384
$ownerKeys=($owner.PSObject.Properties.Name | Sort-Object) -join ','
if($ownerKeys -cne 'created_at,host_fingerprint,installation_id,repository,root,schema_version,services'){throw 'Invalid owner field set'}
# Read timestamp spelling independently of ConvertFrom-Json's version-dependent DateTime coercion.
$ownerRaw=Get-Content -LiteralPath (Join-Path $root 'installation-owner.json') -Raw
$dateMatch=[regex]::Match($ownerRaw,'"created_at"\s*:\s*"(?<date>[^"\\]+)"')
if(-not $dateMatch.Success -or $dateMatch.Groups['date'].Value -cnotmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?(?:Z|\+00:00)$'){throw 'Owner creation timestamp must be explicit UTC'}
$created=[DateTimeOffset]::Parse($dateMatch.Groups['date'].Value,[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::None)
if($created.Offset -ne [TimeSpan]::Zero){throw 'Owner timestamp must be UTC'}
if($owner.schema_version -ne 1 -or $owner.repository -cne 'YehorBabenko88/tester' -or $owner.installation_id -cne 'f23dab63-92c6-4e56-9027-4213c6b4dc60' -or $owner.host_fingerprint -cne '658ac47c38eb17a6' -or [IO.Path]::GetFullPath($owner.root) -ine $root -or @($owner.services).Count -ne 2 -or $owner.services[0] -cne $appName -or $owner.services[1] -cne $pgName){throw 'Foreign root owner'}
Assert-Release
$null=Assert-Owned $python
$null=Assert-Owned $entry
$null=Assert-Owned (Join-Path $root 'runtime')
$logAllowed=[bool]$Install
$null=Assert-Owned (Join-Path $pgData 'PG_VERSION')
if((Get-Content -LiteralPath (Join-Path $pgData 'PG_VERSION') -Raw).Trim() -cne '18'){throw 'Existing PostgreSQL18 cluster required'}
$null=Assert-Plain $pgCtl
$null=Assert-Plain 'C:\Program Files\PostgreSQL\18\bin\postgres.exe'
# Resolve effective config using the trusted installed server, without starting it.
foreach($setting in @(@('port','55469'),@('listen_addresses','127.0.0.1'),@('data_directory',$pgData))){
    $effective=@(& 'C:\Program Files\PostgreSQL\18\bin\postgres.exe' -D $pgData -C $setting[0] 2>&1)
    if($LASTEXITCODE -ne 0 -or $effective.Count -ne 1 -or ($effective[0].ToString().Trim().Replace('/','\') -ine $setting[1])){throw 'PostgreSQL effective scope differs'}
}
if(Get-CimInstance -ClassName Win32_Service -Filter "Name='$appName'"){throw 'Application service already exists; this installer never updates'}
$existing=Inspect-Pg
if($Verify){
    [ordered]@{result='VERIFIED_READ_ONLY';release_sha=$ReleaseSha;manifest_sha256=$ManifestSHA256;postgres_service_present=($null -ne $existing);starts_services=$false}|ConvertTo-Json
    return
}
$principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Run the reviewed installer through normal UAC elevation'}

if($null -eq $existing){
    $stage='PG_REGISTER'
    $output=@(& $pgCtl register -N $pgName -D $pgData -S demand -U 'NT AUTHORITY\LocalService' 2>&1)
    if($LASTEXITCODE -ne 0){throw 'Owned PostgreSQL registration failed; inspect locally, no retry or rollback performed'}
    if($null -eq (Inspect-Pg)){throw 'Post-registration PostgreSQL ownership proof failed'}
}
# Recheck payload immediately before executing it; do not start either service.
Assert-Release
$stage='APP_REGISTER'
$result=@(& $python -I -B $entry install --release $release --manifest-sha256 $ManifestSHA256 --with-postgres-service 2>&1)
if($LASTEXITCODE -ne 0){throw 'Application registration failed; owned PG registration may remain; no removal performed'}
# Sanitized fixed-field log only, exclusive create prevents replacing any existing target/link.
$log=Join-Path $root ('runtime\install-'+[Guid]::NewGuid().ToString('N')+'.json')
$bytes=[Text.UTF8Encoding]::new($false).GetBytes(($plan|ConvertTo-Json -Depth 4))
$stream=[IO.File]::Open($log,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
[ordered]@{result='REGISTERED_NOT_STARTED';release_sha=$ReleaseSha;manifest_sha256=$ManifestSHA256}|ConvertTo-Json
} catch {
    # Never publish native/Python output, DSNs or arbitrary exception diagnostics.
    if($logAllowed){
        try {
            $null=Assert-Owned (Join-Path $root 'runtime')
            $failurePath=Join-Path $root ('runtime\install-failed-'+[Guid]::NewGuid().ToString('N')+'.json')
            $failureBytes=[Text.UTF8Encoding]::new($false).GetBytes((@{result='INSTALL_FAILED';stage=$stage;release_sha=$ReleaseSha;manifest_sha256=$ManifestSHA256}|ConvertTo-Json))
            $failureStream=[IO.File]::Open($failurePath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
            try{$failureStream.Write($failureBytes,0,$failureBytes.Length);$failureStream.Flush($true)}finally{$failureStream.Dispose()}
        } catch { }
    }
    throw ('Scoped installer failed at '+$stage+'; no automatic retry/removal/start. Review owned runtime and local registration state.')
}
