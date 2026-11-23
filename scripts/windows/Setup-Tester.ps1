#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Bundle = $PSScriptRoot,
    [string]$Root = 'C:\TesterLab',
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{64}$')][string]$ManifestSHA256,
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{64}$')][string]$TrustedPublicKeySHA256,
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{40}$')][string]$ExpectedSourceSHA,
    [ValidateSet('production','test')][string]$Mode='production',
    [string]$Desktop=''
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$Principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {throw 'Run Setup-Tester.ps1 as Administrator.'}
function Assert-Plain([string]$Path) {
    $Cursor=Get-Item -LiteralPath $Path -Force
    while ($null -ne $Cursor) {
        if (($Cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {throw 'Reparse path refused.'}
        if ($Cursor -is [IO.FileInfo]) {$Cursor=$Cursor.Directory} else {$Cursor=$Cursor.Parent}
    }
}
Add-Type -TypeDefinition @"
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public sealed class TesterInstallerLease : IDisposable {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    private static extern SafeFileHandle CreateFileW(string path, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError=true)]
    private static extern bool GetFileInformationByHandle(SafeFileHandle handle, out Info info);
    [StructLayout(LayoutKind.Sequential)] private struct Info {
        public uint attributes; public System.Runtime.InteropServices.ComTypes.FILETIME created;
        public System.Runtime.InteropServices.ComTypes.FILETIME accessed; public System.Runtime.InteropServices.ComTypes.FILETIME written;
        public uint volume; public uint sizeHigh; public uint sizeLow; public uint links; public uint indexHigh; public uint indexLow;
    }
    private SafeFileHandle handle;
    public TesterInstallerLease(string path, bool directory) {
        handle = CreateFileW(path, directory ? 0x20081u : 0x80020080u, directory ? 3u : 1u, IntPtr.Zero, 3u, 0x02200000u, IntPtr.Zero);
        if (handle.IsInvalid) { throw new Win32Exception(Marshal.GetLastWin32Error()); }
        Info info;
        if (!GetFileInformationByHandle(handle, out info)) { Dispose(); throw new Win32Exception(Marshal.GetLastWin32Error()); }
        if ((info.attributes & 0x400u) != 0 || (!directory && info.links != 1)) { Dispose(); throw new InvalidOperationException("Reparse or hardlinked maintenance object"); }
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
            $maintenanceLeases.Add((New-Object TesterInstallerLease($parent, $true)))
            $maintenanceLocked[$parent] = $true
        }
    }
    $maintenanceLeases.Add((New-Object TesterInstallerLease($full, [bool]$item.PSIsContainer)))
    $maintenanceLocked[$full] = $maintenanceLeases[$maintenanceLeases.Count - 1]
}

function Protect-InstallerObject([string]$Path) {
    Assert-Plain $Path
    $item=Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer) {$acl=New-Object Security.AccessControl.DirectorySecurity; $inheritance=[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'}
    else {$acl=New-Object Security.AccessControl.FileSecurity; $inheritance=[Security.AccessControl.InheritanceFlags]::None}
    $acl.SetAccessRuleProtection($true,$false)
    $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
    foreach ($sid in @('S-1-5-18','S-1-5-32-544')) {
        $rule=[Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),[Security.AccessControl.FileSystemRights]::FullControl,$inheritance,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow)
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}
$Bundle=[IO.Path]::GetFullPath($Bundle)
Assert-Plain $Bundle
Lock-MaintenancePath $Bundle
$Objects=@(Get-ChildItem -LiteralPath $Bundle -Recurse -Force)
foreach ($Object in $Objects) {
    Lock-MaintenancePath $Object.FullName
}
$EnvelopePath=Join-Path $Bundle 'release-manifest.json'
Assert-Plain $EnvelopePath
# External envelope digest bootstraps trust BEFORE executing bundled Python/code.
if ((Get-FileHash -LiteralPath $EnvelopePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ManifestSHA256) {throw 'Independent manifest fingerprint mismatch.'}
$Envelope=Get-Content -LiteralPath $EnvelopePath -Raw | ConvertFrom-Json
$Files=@(Get-ChildItem -LiteralPath $Bundle -Recurse -Force | Where-Object {-not $_.PSIsContainer -and $_.FullName -ne $EnvelopePath})
if ($Files.Count -ne @($Envelope.manifest.files.PSObject.Properties).Count) {throw 'Bundle inventory count mismatch.'}
foreach ($File in $Files) {
    Assert-Plain $File.FullName
    $Relative=$File.FullName.Substring($Bundle.Length+1).Replace('\','/')
    if ($Relative -match '(^/|\\|:|(^|/)\.\.?(/|$)|//)') {throw 'Unsafe artifact path.'}
    $Expected=$Envelope.manifest.files.PSObject.Properties[$Relative]
    if ($null -eq $Expected -or (Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Expected.Value) {throw 'Bundle artifact mismatch.'}
}
# Inventory is externally authenticated before any ACL change. All files/dirs
# remain leased through child exit; normalize only the verified installer media.
Protect-InstallerObject $Bundle
foreach ($Object in $Objects) {Protect-InstallerObject $Object.FullName}
$FinalObjects=@(Get-ChildItem -LiteralPath $Bundle -Recurse -Force)
if (@(Compare-Object @($Objects.FullName|Sort-Object) @($FinalObjects.FullName|Sort-Object)).Count -ne 0) {throw 'Bundle tree changed during bootstrap protection.'}
Write-Progress -Activity 'TESTER installation' -Status 'Verifying trusted release and provisioning isolated services' -PercentComplete 10
$Python=Join-Path $Bundle 'runtimes\python\python.exe'
$Payload=Join-Path $Bundle 'payload'
$Arguments=@('-I','-B','-c','import sys; sys.path.insert(0,sys.argv.pop(1)); from src.api.release_install import main; main()', $Payload,'--bundle',$Bundle,'--root',$Root,'--trusted-public-key-sha256',$TrustedPublicKeySHA256,'--expected-source-sha',$ExpectedSourceSHA,'--mode',$Mode)
if ($Desktop) {$Arguments+=@('--desktop',$Desktop)}
$PreviousBytecode=$env:PYTHONDONTWRITEBYTECODE
try {
    $env:PYTHONDONTWRITEBYTECODE='1'
    & $Python @Arguments
} finally {$env:PYTHONDONTWRITEBYTECODE=$PreviousBytecode}
if ($LASTEXITCODE -ne 0) {throw 'TESTER installation failed; preserve state/install.json for diagnosis.'}
Write-Progress -Activity 'TESTER installation' -Completed
Write-Output 'TESTER installation ready.'
foreach ($lease in $maintenanceLeases) {$lease.Dispose()}
