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
    param([string]$ItemPath,[string]$CreatorSid,[string]$CreatorRights='',[string]$ServiceRights='',[switch]$NoInheritance,[switch]$PreserveExisting,[switch]$MutableChildren)
    $Item=Get-Item -LiteralPath $ItemPath -Force
    Assert-TesterLabPlain $Item
    # Read the existing descriptor so only DACL changes are marked for persistence.
    # A blank FileSecurity passed through Set-Acl can request SACL/owner writes.
    $Acl=Get-Acl -LiteralPath $ItemPath
    $Inheritance=[Security.AccessControl.InheritanceFlags]::None
    if ($Item.PSIsContainer -and (-not $NoInheritance -or $MutableChildren)) {
        $Inheritance=[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
    }
    if (-not $PreserveExisting) {
        $Acl.SetAccessRuleProtection($true,$false)
        foreach ($OldRule in @($Acl.Access)) { $null=$Acl.RemoveAccessRuleSpecific($OldRule) }
    }
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
    if ($Item.PSIsContainer -and $PreserveExisting) {
        # SetNamedSecurityInfo (used by .NET) auto-propagates to unseen children.
        # SetFileSecurity changes this directory's DACL without that propagation.
        if (-not ('TesterLabOwnerDacl' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class TesterLabOwnerDacl {
    [DllImport("advapi32.dll", EntryPoint="SetFileSecurityW", CharSet=CharSet.Unicode, SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool Set(string path, uint information, byte[] descriptor);
}
'@
        }
        if (-not [TesterLabOwnerDacl]::Set($ItemPath,4,$Acl.GetSecurityDescriptorBinaryForm())) {
            throw [ComponentModel.Win32Exception]::new([Runtime.InteropServices.Marshal]::GetLastWin32Error())
        }
    }
    elseif ($Item.PSIsContainer) { [IO.Directory]::SetAccessControl($ItemPath,$Acl) }
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


function Get-TesterLabPortableRights([string]$Relative,[bool]$IsDirectory) {
    $Top=($Relative -split '/')[0]
    if (-not $Relative -or $Relative -eq 'releases') {return 'Modify'}
    if ($Top -in @('releases','dependencies')) {return 'ReadAndExecute'}
    if ($Top -in @('runtime','postgres-data','backups','state')) {return 'Modify'}
    if ($Top -eq 'private') {if ($IsDirectory) {return 'ReadAndExecute'} else {return 'Read'}}
    if ($Relative -in @('.tester-lab.json','config.json')) {return 'Modify'}
    return ''
}

function Assert-TesterLabNoRegisteredServices([string]$InstallationId) {
    foreach ($Name in @('TesterLabApp_','TesterLabPG_')) {
        $ServiceName=$Name+([Guid]::Parse($InstallationId).ToString('N'))
        try { $null=Get-Service -Name $ServiceName -ErrorAction Stop; throw 'Registered service requires separate reviewed recovery.' }
        catch { if ($_.FullyQualifiedErrorId -notlike 'NoServiceFoundForGivenName*') {throw} }
    }
}

function Write-TesterLabAclJournal($Writer,$Record) {
    $Writer.WriteLine(($Record | ConvertTo-Json -Depth 7 -Compress))
    $Writer.Flush()
    $Writer.BaseStream.Flush($true)
}

function Invoke-TesterLabAclRecovery {
    param([string]$Root,[string]$InstallationId,[string]$ReceiptPath,
          [scriptblock]$VerifyNoServices={param($Id) Assert-TesterLabNoRegisteredServices $Id})
    $Root=[IO.Path]::GetFullPath($Root).TrimEnd('\')
    $RootItem=Get-Item -LiteralPath $Root -Force
    Assert-TesterLabPlain $RootItem
    $Me=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    if ((Get-TesterLabOwnerSid $Root) -ne $Me) {throw 'Only the original individual root owner may recover ACLs.'}
    $MarkerPath=Join-Path $Root '.tester-lab.json'
    Assert-TesterLabPlain (Get-Item -LiteralPath $MarkerPath -Force)
    if ((Get-TesterLabOwnerSid $MarkerPath) -ne $Me) {throw 'Fresh marker must belong to the original individual creator.'}
    $Marker=Get-Content -LiteralPath $MarkerPath -Raw | ConvertFrom-Json
    $Id=[Guid]::Parse($InstallationId).ToString('N')
    if ($Marker.schema -ne 'tester-lab/v1' -or $Marker.root -ne $Root -or $Marker.repository -ne 'YehorBabenko88/tester' -or $Marker.installation_id -ne $InstallationId -or $Marker.services.app -ne ('TesterLabApp_'+$Id) -or $Marker.services.postgres -ne ('TesterLabPG_'+$Id)) {throw 'Foreign or unknown fresh lab marker.'}
    & $VerifyNoServices $InstallationId
    $ReceiptPath=[IO.Path]::GetFullPath($ReceiptPath)
    if ($ReceiptPath.StartsWith($Root+'\',[StringComparison]::OrdinalIgnoreCase) -or $ReceiptPath -eq $Root) {throw 'Receipt must be outside the repaired lab.'}
    if ((Test-Path -LiteralPath $ReceiptPath) -or (Test-Path -LiteralPath ($ReceiptPath+'.jsonl'))) {throw 'Never overwrite recovery evidence.'}
    Assert-TesterLabPlain (Get-Item -LiteralPath ([IO.Path]::GetDirectoryName($ReceiptPath)) -Force)
    $Stream=[IO.File]::Open($ReceiptPath+'.jsonl',[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
    $Writer=[IO.StreamWriter]::new($Stream,[Text.UTF8Encoding]::new($false))
    $Entries=[Collections.Generic.List[object]]::new()
    $Queue=[Collections.Generic.Queue[object]]::new()
    $Queue.Enqueue($RootItem)
    try {
        Write-TesterLabAclJournal $Writer ([ordered]@{kind='header';schema='tester-lab-acl-recovery-journal/v1';installation_id=$InstallationId;root=$Root;creator_sid=$Me;created_utc=[DateTime]::UtcNow.ToString('o')})
        while ($Queue.Count -gt 0) {
            $Item=$Queue.Dequeue()
            $Relative=$Item.FullName.Substring($Root.Length).TrimStart('\').Replace('\','/')
            $Rights=Get-TesterLabPortableRights $Relative $Item.PSIsContainer
            $Entry=[ordered]@{path=$Item.FullName;owner=$null;creator_rights=$Rights;skip='';original_sddl=$null}
            try {Assert-TesterLabPlain $Item} catch {$Entry.skip='reparse-path'}
            if (-not $Entry.skip -and -not $Rights) {$Entry.skip='outside-recovery-scope'}
            if (-not $Entry.skip) {
                try {
                    $Acl=Get-Acl -LiteralPath $Item.FullName
                    $Entry.owner=$Acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
                    $Entry.original_sddl=$Acl.Sddl
                    if ($Entry.owner -ne $Me) {$Entry.skip='foreign-owner'}
                } catch [UnauthorizedAccessException] {$Entry.skip='acl-unreadable'}
            }
            $Entries.Add([pscustomobject]$Entry)
            Write-TesterLabAclJournal $Writer ([ordered]@{kind='snapshot';entry=$Entry})
            if ($Entry.skip) {Write-Output ('SKIPPED '+$Entry.skip+' '+$Entry.path);continue}
            # Win32 DACL-only directory writes avoid propagating into existing children.
            # Mutable directories must nevertheless let future children inherit creator Modify.
            if ((Get-TesterLabOwnerSid $Entry.path) -ne $Me) {throw 'Owner changed during recovery.'}
            Set-TesterLabExplicitAcl -ItemPath $Entry.path -CreatorSid $Me -CreatorRights $Rights -NoInheritance -PreserveExisting:$Item.PSIsContainer -MutableChildren:($Item.PSIsContainer -and $Rights -eq 'Modify')
            Write-TesterLabAclJournal $Writer ([ordered]@{kind='restored';path=$Entry.path})
            if ($Item.PSIsContainer) {
                # Enumerate only this newly readable owned directory, never an entire locked tree.
                foreach ($Child in @(Get-ChildItem -LiteralPath $Item.FullName -Force)) {$Queue.Enqueue($Child)}
            }
        }
        Write-TesterLabAclJournal $Writer ([ordered]@{kind='complete';count=$Entries.Count})
    } finally {$Writer.Dispose()}
    $Receipt=[ordered]@{schema='tester-lab-acl-recovery/v1';installation_id=$InstallationId;root=$Root;creator_sid=$Me;original_acl=@($Entries.ToArray());journal=$ReceiptPath+'.jsonl'}
    $FinalStream=[IO.File]::Open($ReceiptPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    $FinalWriter=[IO.StreamWriter]::new($FinalStream,[Text.UTF8Encoding]::new($false))
    try {$FinalWriter.Write(($Receipt | ConvertTo-Json -Depth 7));$FinalWriter.Flush();$FinalWriter.BaseStream.Flush($true)} finally {$FinalWriter.Dispose()}
    Write-Output ('Original SDDL receipt: '+$ReceiptPath)
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
Invoke-TesterLabAclRecovery -Root $Root -InstallationId $InstallationId -ReceiptPath $ReceiptPath
