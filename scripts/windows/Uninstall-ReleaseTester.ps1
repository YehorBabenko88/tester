# Run from independently verified external maintenance media; never delete by shell recursion.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Root,
    [Parameter(Mandatory=$true)][string]$Ledger,
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{64}$')][string]$TrustedLedgerSha256,
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{64}$')][string]$TrustedPublicKeySha256,
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{40}$')][string]$SourceSha,
    [Parameter(Mandatory=$true)][string]$EvidencePath,
    [Parameter(Mandatory=$true)][string]$ModuleSource,
    [string]$Python = 'python.exe',
    [switch]$ScopeOnly
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$previousPythonPath = $env:PYTHONPATH
$previousGuardians = $env:GUARDIANS_ENABLED
$pythonExecutable = (Get-Command $Python -CommandType Application -ErrorAction Stop).Source
$moduleDirectory = [IO.Path]::GetFullPath($ModuleSource)
$rootDirectory = [IO.Path]::GetFullPath($Root).TrimEnd('\')
foreach ($candidate in @($pythonExecutable, $moduleDirectory)) {
    if ($candidate.Equals($rootDirectory, [StringComparison]::OrdinalIgnoreCase) -or
        $candidate.StartsWith($rootDirectory + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Maintenance interpreter and signed module source must be outside the deleted root.'
    }
}
$arguments = @('-m', 'src.api.release_uninstall', '--root', $Root, '--ledger', $Ledger,
    '--trusted-ledger-sha256', $TrustedLedgerSha256, '--trusted-public-key-sha256', $TrustedPublicKeySha256,
    '--source-sha', $SourceSha, '--evidence-path', $EvidencePath)
if ($ScopeOnly) { $arguments += '--scope-only' }
try {
    $env:PYTHONPATH = $moduleDirectory
    $env:GUARDIANS_ENABLED = 'false'
    Push-Location -LiteralPath $moduleDirectory
    try {
        & $pythonExecutable @arguments
        if ($LASTEXITCODE -ne 0) { throw "TESTER removal refused or partially completed (exit $LASTEXITCODE); inspect external evidence." }
    } finally { Pop-Location }
} finally {
    $env:PYTHONPATH = $previousPythonPath
    $env:GUARDIANS_ENABLED = $previousGuardians
}
