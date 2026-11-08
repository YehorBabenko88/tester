# Published owner-only rollback of this task's failed fresh-lab ACL installation.
# Does not elevate, change ownership, modify bytes, or call SCM mutation APIs.
[CmdletBinding()]
param([switch]$FunctionsOnly,[string]$ReceiptPath='')
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest

function Get-TesterLabOwnerSid([string]$ItemPath) {
    return (Get-Acl -LiteralPath $ItemPath).GetOwner([Security.Principal.SecurityIdentifier]).Value
}

function Assert-TesterLabPlain([IO.FileSystemInfo]$Item) {
    while ($null -ne $Item) {
        if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse path refused.' }
        if ($Item -is [IO.FileInfo]) { $Item=$Item.Directory } else { $Item=$Item.Parent }
    }
}

function Set-TesterLabExplicitAcl {
    param([string]$ItemPath,[string]$CreatorSid,[string]$CreatorRights='',[string]$ServiceRights='')
    $Item=Get-Item -LiteralPath $ItemPath -Force
    Assert-TesterLabPlain $Item
    # Read the existing descriptor so only DACL changes are marked for persistence.
    # A blank FileSecurity passed through Set-Acl can request SACL/owner writes.
    $Acl=Get-Acl -LiteralPath $ItemPath
    $Inheritance=[Security.AccessControl.InheritanceFlags]::None
    if ($Item.PSIsContainer) {
        $Inheritance=[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
    }
    $Acl.SetAccessRuleProtection($true,$false)
    foreach ($OldRule in @($Acl.Access)) { $null=$Acl.RemoveAccessRuleSpecific($OldRule) }
    $Grants=@{'S-1-5-32-544'='FullControl';'S-1-5-18'='FullControl'}
    if ($CreatorRights) { $Grants[$CreatorSid]=$CreatorRights }
    if ($ServiceRights) { $Grants['S-1-5-19']=$ServiceRights }
    foreach ($Entry in $Grants.GetEnumerator()) {
        $Rule=[Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new($Entry.Key),
            [Security.AccessControl.FileSystemRights]$Entry.Value,$Inheritance,
            [Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow)
        $Acl.AddAccessRule($Rule)
    }
    # File rules have no OI/CI flags. Every leaf receives actual effective ACEs.
    # The PowerShell Set-Acl provider can persist extra descriptor sections and
    # demand SeSecurityPrivilege on an empty DACL. .NET persists modified DACL only.
    if ($Item.PSIsContainer) { [IO.Directory]::SetAccessControl($ItemPath,$Acl) }
    else { [IO.File]::SetAccessControl($ItemPath,$Acl) }
}

function Get-TesterLabAclRepairPlan {
    param([string]$Root,[string]$InstallationId)
    $Root=[IO.Path]::GetFullPath($Root).TrimEnd('\')
    $RootItem=Get-Item -LiteralPath $Root -Force
    Assert-TesterLabPlain $RootItem
    $Me=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    if ((Get-TesterLabOwnerSid $Root) -ne $Me) { throw 'Only the original individual root owner may recover ACLs.' }
    $MarkerPath=Join-Path $Root '.tester-lab.json'
    Assert-TesterLabPlain (Get-Item -LiteralPath $MarkerPath -Force)
    if ((Get-TesterLabOwnerSid $MarkerPath) -ne $Me) {throw 'Fresh marker must belong to the original individual creator.'}
    $Marker=Get-Content -LiteralPath $MarkerPath -Raw | ConvertFrom-Json
    if ($Marker.schema -ne 'tester-lab/v1' -or $Marker.root -ne $Root -or $Marker.repository -ne 'YehorBabenko88/tester' -or $Marker.installation_id -ne $InstallationId) { throw 'Foreign or unknown fresh lab marker.' }
    $Id=[Guid]::Parse($InstallationId).ToString('N')
    if ($Marker.services.app -ne ('TesterLabApp_'+$Id) -or $Marker.services.postgres -ne ('TesterLabPG_'+$Id)) {throw 'Foreign service names.'}
    $Entries=@($RootItem)+@(Get-ChildItem -LiteralPath $Root -Recurse -Force)
    $Plan=@()
    foreach ($Item in $Entries) {
        Assert-TesterLabPlain $Item
        try {
            $Acl=Get-Acl -LiteralPath $Item.FullName
            $Owner=$Acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
        } catch [UnauthorizedAccessException] {
            $Plan+=[pscustomobject]@{path=$Item.FullName;owner=$null;creator_rights='';skip='acl-unreadable';original_sddl=$null}
            continue
        }
        $Relative=$Item.FullName.Substring($Root.Length).TrimStart('\').Replace('\','/')
        $Top=($Relative -split '/')[0]
        $Rights=''
        if (-not $Relative) {$Rights='Modify'}
        elseif ($Relative -eq 'releases') {$Rights='Modify'}
        elseif ($Top -in @('releases','dependencies')) {$Rights='ReadAndExecute'}
        elseif ($Top -in @('runtime','postgres-data','backups','state')) {$Rights='Modify'}
        elseif ($Top -eq 'private') {if ($Item.PSIsContainer) {$Rights='ReadAndExecute'} else {$Rights='Read'}}
        elseif ($Relative -in @('.tester-lab.json','config.json')) {$Rights='Modify'}
        $Reason=''
        if ($Owner -ne $Me) {$Reason='foreign-owner'}
        elseif (-not $Rights) {$Reason='outside-recovery-scope'}
        $Plan+= [pscustomobject]@{path=$Item.FullName; owner=$Owner; creator_rights=$Rights; skip=$Reason; original_sddl=$Acl.Sddl}
    }
    return $Plan
}

if ($FunctionsOnly) { return }
$Root='C:\TesterLab'
$InstallationId='89189096-be4c-4cb3-bf2b-07b44f25fd60'
if (-not $ReceiptPath) { throw 'An external -ReceiptPath is required before ACL recovery.' }
$ReceiptPath=[IO.Path]::GetFullPath($ReceiptPath)
$Workspace='C:\Users\Administrator\Documents\Codex\2026-11-08\files-pasted-by-the-user-codex'
if (-not ($ReceiptPath.StartsWith($Workspace+'\work\',[StringComparison]::OrdinalIgnoreCase) -or $ReceiptPath.StartsWith($Workspace+'\outputs\',[StringComparison]::OrdinalIgnoreCase))) { throw 'Receipt must reside in this task work or outputs directory.' }
if (Test-Path -LiteralPath $ReceiptPath) { throw 'Never overwrite an existing recovery receipt.' }
Assert-TesterLabPlain (Get-Item -LiteralPath ([IO.Path]::GetDirectoryName($ReceiptPath)) -Force)
$Plan=@(Get-TesterLabAclRepairPlan -Root $Root -InstallationId $InstallationId)
# Recovery is portable only: refuse any registered UUID service, even stopped.
foreach ($Name in @('TesterLabApp_','TesterLabPG_')) {
    $ServiceName=$Name+([Guid]::Parse($InstallationId).ToString('N'))
    try { $null=Get-Service -Name $ServiceName -ErrorAction Stop; throw 'Registered service requires separate reviewed recovery.' }
    catch { if ($_.FullyQualifiedErrorId -notlike 'NoServiceFoundForGivenName*') {throw} }
}
$Receipt=[ordered]@{schema='tester-lab-acl-recovery/v1';installation_id=$InstallationId;root=$Root;creator_sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;created_utc=[DateTime]::UtcNow.ToString('o');original_acl=$Plan}
$Json=$Receipt | ConvertTo-Json -Depth 6
$Stream=[IO.File]::Open($ReceiptPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
$Writer=[IO.StreamWriter]::new($Stream,[Text.UTF8Encoding]::new($false))
try {$Writer.Write($Json)} finally {$Writer.Dispose()}
foreach ($Entry in $Plan) {
    if ($Entry.skip) { Write-Output ('SKIPPED '+$Entry.skip+' '+$Entry.path); continue }
    # Ownership/reparse checks repeated immediately before each WRITE_DAC.
    if ((Get-TesterLabOwnerSid $Entry.path) -ne $Receipt.creator_sid) {throw 'Owner changed during recovery.'}
    Set-TesterLabExplicitAcl -ItemPath $Entry.path -CreatorSid $Receipt.creator_sid -CreatorRights $Entry.creator_rights
    Write-Output ('RESTORED '+$Entry.path)
}
Write-Output ('Original SDDL receipt: '+$ReceiptPath)
