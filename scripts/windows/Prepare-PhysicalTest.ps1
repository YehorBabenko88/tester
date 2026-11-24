[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][Guid]$InstallationId,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{16}$')][string]$HostFingerprint,
    [switch]$Create
)
$ErrorActionPreference='Stop'
$root='C:\ProgramData\TesterPhysicalTest20261107'
$services=@('TesterPhysicalTest20261107','TesterPhysicalPg20261107')
$marker=Join-Path $root 'installation-owner.json'
$plan=[ordered]@{root=$root;installation_id=$InstallationId.ToString();host_fingerprint=$HostFingerprint;services=$services;api_port=18765;postgres_port=55469;physical_changes=[bool]$Create;deletes_data=$false;changes_services=$false;changes_global_security=$false}
if(-not $Create){$plan|ConvertTo-Json -Depth 5;return}

function Assert-OwnedPath([string]$path){
    $full=[IO.Path]::GetFullPath($path)
    if($full -ne $root -and -not $full.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Path escapes fixed physical test root'}
    $cursor=$full
    while($cursor.Length -ge $root.Length){
        if(Test-Path -LiteralPath $cursor){
            if((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Reparse point in owned path'}
        }
        if($cursor -eq $root){break}
        $cursor=[IO.Path]::GetDirectoryName($cursor)
    }
    return $full
}

if(Test-Path -LiteralPath $root){
    $null=Assert-OwnedPath $root
    if(-not (Test-Path -LiteralPath $marker -PathType Leaf)){throw 'Existing root has no ownership marker; refusing to adopt it'}
    $null=Assert-OwnedPath $marker
    $owner=Get-Content -LiteralPath $marker -Raw|ConvertFrom-Json
    if($owner.schema_version -ne 1 -or $owner.repository -ne 'YehorBabenko88/tester' -or
       $owner.installation_id -ne $InstallationId.ToString() -or $owner.host_fingerprint -ne $HostFingerprint -or
       [IO.Path]::GetFullPath($owner.root) -ne $root -or
       @($owner.services).Count -ne 2 -or $owner.services[0] -ne $services[0] -or $owner.services[1] -ne $services[1]){throw 'Foreign or incompatible ownership marker'}
}else{
    # No -Force: another installer winning this path race must make us stop.
    $null=New-Item -ItemType Directory -Path $root
    $owner=[ordered]@{schema_version=1;repository='YehorBabenko88/tester';installation_id=$InstallationId.ToString();host_fingerprint=$HostFingerprint;root=$root;services=$services;created_at=(Get-Date).ToUniversalTime().ToString('o')}
    $json=$owner|ConvertTo-Json -Depth 5
    $bytes=[Text.UTF8Encoding]::new($false).GetBytes($json)
    $stream=[IO.File]::Open($marker,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
}

$current=[Security.Principal.WindowsIdentity]::GetCurrent().User
$system=[Security.Principal.SecurityIdentifier]'S-1-5-18'
$localService=[Security.Principal.SecurityIdentifier]'S-1-5-19'
$administrators=[Security.Principal.SecurityIdentifier]'S-1-5-32-544'
$inherit=[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
$propagation=[Security.AccessControl.PropagationFlags]::None
function Set-OwnedAcl([string]$path,[string]$serviceRights,[string]$adminRights='ReadAndExecute'){
    $path=Assert-OwnedPath $path
    $acl=[Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true,$false)
    $acl.SetOwner($current)
    foreach($sid in @($current,$system)){
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl',$inherit,$propagation,'Allow'))
    }
    if($serviceRights){$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($localService,$serviceRights,$inherit,$propagation,'Allow'))}
    if($adminRights){$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($administrators,$adminRights,$inherit,$propagation,'Allow'))}
    Set-Acl -LiteralPath $path -AclObject $acl
}

# Only fresh test-owned resources; never modify the parent ProgramData ACL.
Set-OwnedAcl $root 'ReadAndExecute'
foreach($name in @('private','runtime','state','releases','postgres-data','backups','venv')){
    $path=Assert-OwnedPath (Join-Path $root $name)
    if(-not(Test-Path -LiteralPath $path)){$null=New-Item -ItemType Directory -Path $path}
    elseif(-not(Test-Path -LiteralPath $path -PathType Container)){throw 'Expected an owned directory'}
    $rights=if($name -in @('runtime','postgres-data','backups')){'Modify'}elseif($name -in @('private','state')){''}else{'ReadAndExecute'}
    $adminRights=if($name -in @('private','state')){''}elseif($name -in @('runtime','backups')){'Modify'}else{'ReadAndExecute'}
    Set-OwnedAcl $path $rights $adminRights
}
$plan|ConvertTo-Json -Depth 5
