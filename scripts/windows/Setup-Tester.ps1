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
$Bundle=[IO.Path]::GetFullPath($Bundle)
Assert-Plain $Bundle
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
Write-Progress -Activity 'TESTER installation' -Status 'Verifying trusted release and provisioning isolated services' -PercentComplete 10
$Python=Join-Path $Bundle 'runtimes\python\python.exe'
$Payload=Join-Path $Bundle 'payload'
$Arguments=@('-I','-B','-c','import sys; sys.path.insert(0,sys.argv.pop(1)); from src.api.release_install import main; main()', $Payload,'--bundle',$Bundle,'--root',$Root,'--trusted-public-key-sha256',$TrustedPublicKeySHA256,'--expected-source-sha',$ExpectedSourceSHA,'--mode',$Mode)
if ($Desktop) {$Arguments+=@('--desktop',$Desktop)}
& $Python @Arguments
if ($LASTEXITCODE -ne 0) {throw 'TESTER installation failed; preserve state/install.json for diagnosis.'}
Write-Progress -Activity 'TESTER installation' -Completed
Write-Output 'TESTER installation ready.'
