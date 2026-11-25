# This protected launcher hashes all external executable/module bytes BEFORE executing them.
[CmdletBinding()]
param(
    [string]$TrustConfig,
    [string]$Root,
    [string]$Ledger,
    [string]$TrustedLedgerSha256,
    [string]$TrustedPublicKeySha256,
    [string]$SourceSha,
    [string]$EvidencePath,
    [string]$ModuleSource,
    [string]$Python,
    [string]$Bundle,
    [string]$ManifestSha256,
    [switch]$ScopeOnly
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
# A shortcut or console may start this process inside the installation root. The
# process current directory is an open handle that blocks the final root deletion.
[Environment]::CurrentDirectory = [Environment]::SystemDirectory
Set-Location -LiteralPath ([Environment]::SystemDirectory)

# Only trusted, installed launcher code reaches this bootstrap; no bundle code executes here.
Add-Type -TypeDefinition @"
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public sealed class TesterMaintenanceLease : IDisposable {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    private static extern SafeFileHandle CreateFileW(string path, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError=true)]
    private static extern bool GetFileInformationByHandle(SafeFileHandle handle, out Info info);
    [StructLayout(LayoutKind.Sequential)] private struct Info {
        public uint attributes; public System.Runtime.InteropServices.ComTypes.FILETIME created;
        public System.Runtime.InteropServices.ComTypes.FILETIME accessed; public System.Runtime.InteropServices.ComTypes.FILETIME written;
        public uint volume; public uint sizeHigh; public uint sizeLow; public uint links; public uint indexHigh; public uint indexLow;
    }
    [DllImport("kernel32.dll", SetLastError=true)]
    private static extern bool SetFileInformationByHandle(SafeFileHandle handle, int kind, ref byte value, uint size);
    private SafeFileHandle handle;
    public string Identity { get; private set; }
    public TesterMaintenanceLease(string path, bool directory) : this(path, directory, false) {}
    public TesterMaintenanceLease(string path, bool directory, bool delete) {
        uint access = directory ? 0x20081u : 0x80020080u;
        if (delete) { access |= 0x10000u; }
        handle = CreateFileW(path, access, directory ? 3u : 1u, IntPtr.Zero, 3u, 0x02200000u, IntPtr.Zero);
        if (handle.IsInvalid) { throw new Win32Exception(Marshal.GetLastWin32Error()); }
        Info info;
        if (!GetFileInformationByHandle(handle, out info)) { Dispose(); throw new Win32Exception(Marshal.GetLastWin32Error()); }
        if ((info.attributes & 0x400u) != 0 || (!directory && info.links != 1)) { Dispose(); throw new InvalidOperationException("Reparse or hardlinked maintenance object"); }
        Identity = info.volume.ToString() + ":" + info.indexHigh.ToString() + ":" + info.indexLow.ToString();
    }
    public static string GetIdentity(SafeFileHandle file) {
        Info info;
        if (!GetFileInformationByHandle(file, out info)) { throw new Win32Exception(Marshal.GetLastWin32Error()); }
        return info.volume.ToString() + ":" + info.indexHigh.ToString() + ":" + info.indexLow.ToString();
    }
    public void Delete() {
        byte disposition = 1;
        if (!SetFileInformationByHandle(handle, 4, ref disposition, 1)) { throw new Win32Exception(Marshal.GetLastWin32Error()); }
        Dispose();
    }
    public void Dispose() { if (handle != null) { handle.Dispose(); handle = null; } }
}
"@
$maintenanceLeases = New-Object 'System.Collections.Generic.List[System.IDisposable]'
$maintenanceLocked = @{}
trap {
    foreach ($lease in $maintenanceLeases) { $lease.Dispose() }
    throw
}
function Lock-MaintenancePath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    if ($maintenanceLocked.ContainsKey($full)) { return }
    $item = Get-Item -LiteralPath $full -Force
    $ancestors = New-Object 'System.Collections.Generic.Stack[string]'
    if ($item -is [IO.FileInfo]) { $directory = $item.Directory } else { $directory = $item.Parent }
    while ($null -ne $directory) { $ancestors.Push($directory.FullName); $directory = $directory.Parent }
    while ($ancestors.Count) {
        $parent = $ancestors.Pop()
        if (-not $maintenanceLocked.ContainsKey($parent)) {
            $maintenanceLeases.Add((New-Object TesterMaintenanceLease($parent, $true)))
            $maintenanceLocked[$parent] = $true
        }
    }
    $maintenanceLeases.Add((New-Object TesterMaintenanceLease($full, [bool]$item.PSIsContainer)))
    $maintenanceLocked[$full] = $maintenanceLeases[$maintenanceLeases.Count - 1]
}

function Assert-PlainPath([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force
    while ($null -ne $item) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse maintenance paths are forbidden.' }
        if ($item.PSObject.Properties['LinkType'] -and $item.LinkType -eq 'HardLink') { throw 'Hardlinked maintenance files are forbidden.' }
        if ($item -is [IO.FileInfo]) { $item = $item.Directory } else { $item = $item.Parent }
    }
}

function Assert-AdminTrust([string]$Path, [bool]$StrictConfig = $false) {
    Assert-PlainPath $Path
    $acl = Get-Acl -LiteralPath $Path
    $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($owner -notin @('S-1-5-18', 'S-1-5-32-544')) { throw 'Maintenance objects require SYSTEM/Administrators ownership.' }
    $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    if ($StrictConfig -and (-not $acl.AreAccessRulesProtected -or $rules.Count -ne 2)) { throw 'Trust config must have a protected two-principal ACL.' }
    $seen = @{}
    foreach ($rule in $rules) {
        $sid = $rule.IdentityReference.Value
        $admin = $sid -in @('S-1-5-18', 'S-1-5-32-544')
        if ($StrictConfig) {
            if (-not $admin -or $rule.AccessControlType -ne 'Allow' -or [int]$rule.FileSystemRights -ne 0x1f01ff -or $seen.ContainsKey($sid)) { throw 'Foreign rights in trust config ACL.' }
            $seen[$sid] = $true
        } elseif ($rule.AccessControlType -eq 'Allow' -and -not $admin -and (([int64]$rule.FileSystemRights -band 0x500d0156) -ne 0)) {
            throw 'External executable media permits a foreign writer.'
        }
    }
}


function Set-AdminTrust([string]$Path, [bool]$Directory) {
    $acl = Get-Acl -LiteralPath $Path
    $sddl = if ($Directory) { 'O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)' } else { 'O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)' }
    $acl.SetSecurityDescriptorSddlForm($sddl)
    Set-Acl -LiteralPath $Path -AclObject $acl
    Assert-AdminTrust $Path $true
}

function Add-StageRecord([string]$Path, [bool]$Directory) {
    $lease = New-Object TesterMaintenanceLease($Path, $Directory)
    $stageRecords.Add([PSCustomObject]@{ Path=$Path; Directory=$Directory; Identity=$lease.Identity; Lease=$lease })
    if ((Get-Variable recoveryStream -ErrorAction SilentlyContinue) -and $null -ne $recoveryStream) {
        Write-RecoveryRecord ([ordered]@{kind='created';path=$Path;directory=$Directory;identity=$lease.Identity})
    }
}

function Remove-TransientMaintenance {
    # Every directory remains pinned until its own exact identity is deleted.
    # New foreign files are never enumerated for deletion; rmdir fails if they exist.
    foreach ($record in @($stageRecords | Sort-Object { $_.Path.Length } -Descending)) {
        $record.Lease.Dispose()
        $deletion = New-Object TesterMaintenanceLease($record.Path, $record.Directory, $true)
        try {
            if ($deletion.Identity -cne $record.Identity) { throw 'Temporary maintenance identity swapped.' }
            $deletion.Delete()
        } finally { $deletion.Dispose() }
    }
}

function Write-RemovalEvent([string]$Status, [string]$ErrorType = '') {
    $record = [ordered]@{schema='tester-removal/v1';root=$Root;ledger_sha256=$TrustedLedgerSha256;status=$Status;maintenance_cleanup=$Status;error_type=$ErrorType}
    $bytes = [Text.Encoding]::UTF8.GetBytes(($record | ConvertTo-Json -Compress) + "`n")
    $stream = New-Object IO.FileStream($EvidencePath, [IO.FileMode]::Append, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
}

function Write-RecoveryRecord($Record) {
    $encoded = [Text.Encoding]::UTF8.GetBytes(($Record | ConvertTo-Json -Compress) + "`n")
    $recoveryStream.Write($encoded, 0, $encoded.Length)
    $recoveryStream.Flush($true)
}

function Remove-ProtectedMetadata([string]$Path, [string]$ExpectedIdentity) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    Assert-AdminTrust $Path $true
    $handle = New-Object TesterMaintenanceLease($Path, $false, $true)
    try {
        if ([String]::IsNullOrEmpty($ExpectedIdentity) -or $handle.Identity -cne $ExpectedIdentity) { throw 'Protected metadata identity changed.' }
        $handle.Delete()
    } finally { $handle.Dispose() }
}

function Remove-OwnedMaintenanceDirectory([string]$Directory, [string]$InstallationId, [string]$ConfigIdentity) {
    # Full removal: only known metadata names of exactly this installation are
    # deleted; any unknown object preserves the directory and fails loudly.
    $full = [IO.Path]::GetFullPath($Directory).TrimEnd('\')
    if ([IO.Path]::GetFileName($full) -cne ('TesterReleaseMaintenance_' + ([Guid]$InstallationId).ToString('N'))) { throw 'Maintenance directory is not bound to this installation.' }
    [Environment]::CurrentDirectory = [Environment]::SystemDirectory
    Set-Location -LiteralPath ([Environment]::SystemDirectory)
    Assert-AdminTrust $full $true
    $pattern = '^(provisioning\.jsonl|provisioning\.rollback(-retry-[0-9a-f]{32})?\.jsonl|ownership(-[0-9a-f]{32})?\.json|trust\.json|trust\.pending|test-pause\.(json|pending)|maintenance-recovery\.jsonl|evidence|evidence\\removal-[0-9a-f]{32}\.jsonl)$'
    $records = New-Object 'System.Collections.Generic.List[object]'
    $top = New-Object TesterMaintenanceLease($full, $true, $true)
    try {
        $items = @(Get-ChildItem -LiteralPath $full -Recurse -Force)
        foreach ($item in $items) {
            $relative = $item.FullName.Substring($full.Length + 1)
            if ($relative -cnotmatch $pattern) { throw ('Unknown maintenance object preserved: ' + $relative) }
            if ([bool]$item.PSIsContainer -ne ($relative -ceq 'evidence')) { throw 'Unexpected maintenance object kind.' }
            $lease = New-Object TesterMaintenanceLease($item.FullName, [bool]$item.PSIsContainer, $true)
            if ($relative -ceq 'trust.json' -and $lease.Identity -cne $ConfigIdentity) { $lease.Dispose(); throw 'Protected trust identity changed.' }
            $rank = switch ($relative) { 'trust.json' {1} 'maintenance-recovery.jsonl' {2} 'evidence' {3} 'provisioning.jsonl' {4} default {0} }
            $records.Add([PSCustomObject]@{Rank=$rank; Relative=$relative; Lease=$lease})
        }
        if (@(Get-ChildItem -LiteralPath $full -Recurse -Force).Count -ne $records.Count) { throw 'Maintenance directory changed during removal.' }
        # Audit files first; trust last-but-journal so an interrupted cleanup stays
        # resumable (trust + recovery record) or provable by Setup (journal only).
        foreach ($record in @($records | Sort-Object Rank, @{Expression={$_.Relative.Length}; Descending=$true})) { $record.Lease.Delete() }
        $top.Delete()
    } finally {
        foreach ($record in $records) { $record.Lease.Dispose() }
        $top.Dispose()
    }
}

function Write-FinalCleanupRecord([string]$Path) {
    $encoded = [Text.Encoding]::UTF8.GetBytes(([ordered]@{kind='final-cleanup';root=$Root;ledger_sha256=$TrustedLedgerSha256} | ConvertTo-Json -Compress) + "`n")
    $stream = New-Object IO.FileStream($Path, [IO.FileMode]::Append, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    try { $stream.Write($encoded, 0, $encoded.Length); $stream.Flush($true) } finally { $stream.Dispose() }
}

function Complete-Removal {
    foreach ($lease in $maintenanceLeases) { $lease.Dispose() }
    $maintenanceLeases.Clear(); $maintenanceLocked.Clear()
    Remove-OwnedMaintenanceDirectory $maintenanceParent $config.installation_id $configIdentity
    Write-Output (([ordered]@{schema='tester-removal/v1';root=$Root;ledger_sha256=$TrustedLedgerSha256;status='removed';maintenance_cleanup='removed'}) | ConvertTo-Json -Compress)
}

if (-not $TrustConfig) { throw 'Use the protected external -TrustConfig created by the installer.' }
Lock-MaintenancePath $TrustConfig
$configIdentity = $maintenanceLocked[[IO.Path]::GetFullPath($TrustConfig)].Identity
Assert-AdminTrust $TrustConfig $true
$config = Get-Content -LiteralPath $TrustConfig -Raw -Encoding UTF8 | ConvertFrom-Json
if ($config.schema -ne 'tester-maintenance-trust/v1') { throw 'Unknown maintenance trust schema.' }
if ($Root -and [IO.Path]::GetFullPath($Root) -ne [IO.Path]::GetFullPath($config.root)) { throw 'Requested root differs from protected trust config.' }
$Root = $config.root
$Ledger = $config.ledger
$TrustedLedgerSha256 = $config.trusted_ledger_sha256
$TrustedPublicKeySha256 = $config.trusted_public_key_sha256
$SourceSha = $config.source_sha
$ManifestSha256 = $config.manifest_sha256
$maintenanceParent = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($TrustConfig))
Assert-AdminTrust $maintenanceParent $true
$recoveryPath = Join-Path $maintenanceParent 'maintenance-recovery.jsonl'
if (-not (Test-Path -LiteralPath $Root) -and (Test-Path -LiteralPath $recoveryPath)) {
    # Resume an interrupted final cleanup: the ledger may already be deleted.
    Assert-AdminTrust $recoveryPath $true
    $final = @(Get-Content -LiteralPath $recoveryPath -Encoding UTF8 | Where-Object { $_ } | ForEach-Object { $_ | ConvertFrom-Json } | Where-Object { $_.PSObject.Properties['kind'] -and $_.kind -eq 'final-cleanup' -and $_.root -ceq $Root -and $_.ledger_sha256 -ceq $TrustedLedgerSha256 })
    if ($final.Count -gt 0) { Complete-Removal; exit 0 }
}
Lock-MaintenancePath $Ledger
Assert-AdminTrust $Ledger $true
foreach ($pin in @($TrustedLedgerSha256, $TrustedPublicKeySha256, $ManifestSha256)) {
    if ($pin -cnotmatch '^[0-9a-f]{64}$') { throw 'Independent SHA256 pins are required.' }
}
if ($SourceSha -cnotmatch '^[0-9a-f]{40}$') { throw 'Exact approved source SHA is required.' }
if ((Get-FileHash -LiteralPath $Ledger -Algorithm SHA256).Hash.ToLowerInvariant() -cne $TrustedLedgerSha256) { throw 'Protected ownership ledger digest mismatch.' }
$ownership = Get-Content -LiteralPath $Ledger -Raw -Encoding UTF8 | ConvertFrom-Json
if ($ownership.schema -ne 'tester-ownership/v1' -or $ownership.root -cne $Root -or $ownership.installation_id -cne $config.installation_id -or $ownership.release_manifest_sha256 -cne $ManifestSha256) { throw 'Ownership ledger belongs to another installation.' }
if ($ownership.release_envelope.manifest.source_sha -cne $SourceSha -or $ownership.release_envelope.manifest.release_version -ne '1') { throw 'Signed source or version differs from approved trust.' }
$EvidencePath = Join-Path $config.evidence_directory ('removal-' + [Guid]::NewGuid().ToString('N') + '.jsonl')
Assert-AdminTrust $config.evidence_directory $true
$rootDirectory = [IO.Path]::GetFullPath($Root).TrimEnd('\')
$recoveryStream = $null
if (Test-Path -LiteralPath $recoveryPath) {
    Lock-MaintenancePath $recoveryPath
    $recoveryIdentity = $maintenanceLocked[[IO.Path]::GetFullPath($recoveryPath)].Identity
    Assert-AdminTrust $recoveryPath $true
    $records = @(Get-Content -LiteralPath $recoveryPath -Encoding UTF8 | Where-Object { $_ } | ForEach-Object { $_ | ConvertFrom-Json })
    if ($records.Count -lt 1) { throw 'Empty maintenance recovery journal.' }
    $header = $records[0]
    if ($header.schema -ne 'tester-maintenance-recovery/v1' -or $header.root -cne $Root -or $header.ledger_sha256 -cne $TrustedLedgerSha256) { throw 'Foreign maintenance recovery journal.' }
    $stagePrefix = [IO.Path]::GetFullPath($header.stage_root).TrimEnd('\')
    if ([IO.Path]::GetDirectoryName($stagePrefix) -ne $maintenanceParent -or [IO.Path]::GetFileName($stagePrefix) -notmatch '^maintenance-[a-f0-9]{32}$') { throw 'Recovery staging scope escaped protected parent.' }
    $stageRecords = New-Object 'System.Collections.Generic.List[object]'
    $seen = @{}
    foreach ($record in @($records | Select-Object -Skip 1)) {
        if ($record.kind -eq 'final-cleanup') { continue }
        $path = [IO.Path]::GetFullPath($record.path)
        if ($record.kind -ne 'created' -or $seen.ContainsKey($path) -or (-not $path.Equals($stagePrefix, [StringComparison]::OrdinalIgnoreCase) -and -not $path.StartsWith($stagePrefix + '\', [StringComparison]::OrdinalIgnoreCase))) { throw 'Unsafe recovery creation receipt.' }
        $seen[$path]=$true
        if (-not (Test-Path -LiteralPath $path)) { continue }
        Assert-AdminTrust $path
        $lease = New-Object TesterMaintenanceLease($path, [bool]$record.directory)
        if ($lease.Identity -cne $record.identity) { $lease.Dispose(); throw 'Recovery object identity changed.' }
        $stageRecords.Add([PSCustomObject]@{Path=$path;Directory=[bool]$record.directory;Identity=$lease.Identity;Lease=$lease})
    }
    foreach ($lease in $maintenanceLeases) { $lease.Dispose() }
    $maintenanceLeases.Clear(); $maintenanceLocked.Clear()
    try { Remove-TransientMaintenance } finally { foreach ($record in $stageRecords) { $record.Lease.Dispose() } }
    if (-not (Test-Path -LiteralPath $Root)) {
        if ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($header.evidence_path)) -ne [IO.Path]::GetFullPath($config.evidence_directory)) { throw 'Foreign recovery evidence path.' }
        $proof = @(Get-Content -LiteralPath $header.evidence_path -Encoding UTF8 | Where-Object { $_ } | ForEach-Object { $_ | ConvertFrom-Json } | Where-Object { $_.status -eq 'root_removed_pending_maintenance_cleanup' -and $_.root -ceq $Root -and $_.ledger_sha256 -ceq $TrustedLedgerSha256 })
        if ($proof.Count -eq 0) { throw 'Root absence has no completed native-removal evidence.' }
        $EvidencePath = $header.evidence_path
        Write-FinalCleanupRecord $recoveryPath
        Complete-Removal
        exit 0
    }
    Remove-ProtectedMetadata $recoveryPath $recoveryIdentity
}
if (-not (Test-Path -LiteralPath $Root)) {
    # Root already removed without a recovery journal (older or interrupted
    # cleanup): finish only with durable native-removal evidence for this ledger.
    $proofs = @(Get-ChildItem -LiteralPath $config.evidence_directory -Filter 'removal-*.jsonl' -File -Force | ForEach-Object { Get-Content -LiteralPath $_.FullName -Encoding UTF8 | Where-Object { $_ } | ForEach-Object { $_ | ConvertFrom-Json } } | Where-Object { $_.status -in @('root_removed_pending_maintenance_cleanup','removed') -and $_.root -ceq $Root -and $_.ledger_sha256 -ceq $TrustedLedgerSha256 })
    if ($proofs.Count -eq 0) { throw 'Installation root is absent without native-removal evidence.' }
    Complete-Removal
    exit 0
}
$stageRoot = Join-Path $maintenanceParent ('maintenance-' + [Guid]::NewGuid().ToString('N'))
$stageRecords = New-Object 'System.Collections.Generic.List[object]'
$previousPythonPath = $env:PYTHONPATH
$previousGuardians = $env:GUARDIANS_ENABLED
$previousBytecode = $env:PYTHONDONTWRITEBYTECODE
$childPassed = $false
$cleanupPassed = $false
Set-Location -LiteralPath $maintenanceParent
try {
    $recoveryStream = New-Object IO.FileStream($recoveryPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    $recoveryIdentity = [TesterMaintenanceLease]::GetIdentity($recoveryStream.SafeFileHandle)
    Set-AdminTrust $recoveryPath $false
    Write-RecoveryRecord ([ordered]@{schema='tester-maintenance-recovery/v1';root=$Root;ledger_sha256=$TrustedLedgerSha256;stage_root=$stageRoot;evidence_path=$EvidencePath})
    New-Item -ItemType Directory -Path $stageRoot -ErrorAction Stop | Out-Null
    Add-StageRecord $stageRoot $true
    Set-AdminTrust $stageRoot $true
    $createdDirectories = @{$stageRoot=$true}
    $files = @($ownership.maintenance_files.PSObject.Properties)
    if ($files.Count -eq 0) { throw 'No pinned installed maintenance code/runtime inventory.' }
    foreach ($property in $files) {
        $name = $property.Name
        if ($name -match '(^/|\\|:|(^|/)\.\.?(/|$))' -or $property.Value -cnotmatch '^[0-9a-f]{64}$') { throw 'Unsafe maintenance staging inventory.' }
        if (-not ($name.StartsWith('releases/' + $SourceSha + '/') -or $name.StartsWith('dependencies/python/') -or $name.StartsWith('dependencies/venv/Lib/site-packages/'))) { throw 'User data cannot be maintenance payload.' }
        $source = Join-Path $rootDirectory $name
        Lock-MaintenancePath $source
        Assert-AdminTrust $source
        if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant() -cne $property.Value) { throw 'Installed maintenance bytes differ from pinned creation provenance.' }
        $target = Join-Path $stageRoot $name
        $parts = $name.Split('/')
        $directory = $stageRoot
        for ($i=0; $i -lt $parts.Length-1; $i++) {
            $directory = Join-Path $directory $parts[$i]
            if (-not $createdDirectories.ContainsKey($directory)) {
                New-Item -ItemType Directory -Path $directory -ErrorAction Stop | Out-Null
                Add-StageRecord $directory $true
                Set-AdminTrust $directory $true
                $createdDirectories[$directory]=$true
            }
        }
        [IO.File]::Copy($source, $target, $false)
        Add-StageRecord $target $false
        Set-AdminTrust $target $false
        if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -cne $property.Value) { throw 'Staged maintenance copy differs from pinned inventory.' }
    }
    $pythonExecutable = Join-Path $stageRoot 'dependencies/python/python.exe'
    $moduleDirectory = Join-Path $stageRoot ('releases/' + $SourceSha)
    $sitePackages = Join-Path $stageRoot 'dependencies/venv/Lib/site-packages'
    if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf) -or -not (Test-Path -LiteralPath $sitePackages -PathType Container) -or -not (Test-Path -LiteralPath (Join-Path $moduleDirectory 'src/api/release_uninstall.py') -PathType Leaf)) { throw 'Incomplete pinned maintenance runtime.' }
    # Source root must be unlocked before the child can remove it. Only transient
    # code/runtime objects remain pinned through execution; config parent is ACL protected.
    foreach ($lease in $maintenanceLeases) { $lease.Dispose() }
    $maintenanceLeases.Clear()
    $maintenanceLocked.Clear()
    $env:PYTHONPATH = ''
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:GUARDIANS_ENABLED = 'false'
    $bootstrap = "import sys,site,runpy;sys.path.insert(0,sys.argv.pop(1));site.addsitedir(sys.argv.pop(1));runpy.run_module('src.api.release_uninstall',run_name='__main__')"
    $arguments = @('-I', '-B', '-S', '-c', $bootstrap, $moduleDirectory, $sitePackages, '--root', $Root, '--ledger', $Ledger,
        '--trusted-ledger-sha256', $TrustedLedgerSha256, '--trusted-public-key-sha256', $TrustedPublicKeySha256,
        '--source-sha', $SourceSha, '--evidence-path', $EvidencePath, '--defer-completion')
    if ($ScopeOnly) { $arguments += '--scope-only' }
    & $pythonExecutable @arguments
    if ($LASTEXITCODE -ne 0) { throw "TESTER removal refused or partial (exit $LASTEXITCODE)." }
    $childPassed = $true
} finally {
    foreach ($lease in $maintenanceLeases) { $lease.Dispose() }
    try {
        if ($null -ne $recoveryStream) { $recoveryStream.Dispose(); $recoveryStream=$null }
        Remove-TransientMaintenance
        $cleanupPassed = $true
    } catch {
        Write-RemovalEvent 'refused_or_partial' 'MaintenanceCleanupFailure'
        throw
    } finally {
        foreach ($record in $stageRecords) { $record.Lease.Dispose() }
        $env:PYTHONPATH = $previousPythonPath
        $env:GUARDIANS_ENABLED = $previousGuardians
        $env:PYTHONDONTWRITEBYTECODE = $previousBytecode
    }
    if ($cleanupPassed) {
        if ($childPassed -and -not $ScopeOnly) {
            Write-FinalCleanupRecord $recoveryPath
            Complete-Removal
        } else {
            Remove-ProtectedMetadata $recoveryPath $recoveryIdentity
        }
    }
}
if (-not $childPassed -or -not $cleanupPassed) { throw 'Full removal was not confirmed.' }
