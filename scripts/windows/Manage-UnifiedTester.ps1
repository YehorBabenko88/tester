# Disposable process fixture only. Never installs services or self-elevates.
[CmdletBinding()]
param(
    [ValidateSet('plan','status','start','stop','uninstall')][string]$Action='plan',
    [string]$Root,
    [string]$Desktop,
    [string]$DisposableParent,
    [string]$ManifestSHA256,
    [string]$Python='python'
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$Repository=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$PreviousPythonPath=$env:PYTHONPATH
$env:PYTHONPATH=$Repository
if ($Action -eq 'plan') { $ControllerDirectory=[IO.Path]::GetTempPath() } else { $ControllerDirectory=$DisposableParent }
Push-Location -LiteralPath $ControllerDirectory
$PreviousNativeDirectory=[Environment]::CurrentDirectory
[Environment]::CurrentDirectory=$ControllerDirectory
try {
    if ($Action -eq 'plan') {
        & $Python -B -m src.api.unified_windows_install plan
    } else {
        & $Python -B -m src.api.unified_windows_install $Action --root $Root --desktop $Desktop --disposable-parent $DisposableParent --manifest-sha256 $ManifestSHA256
    }
    if ($LASTEXITCODE -ne 0) { throw 'Unified disposable lifecycle failed; inspect local fixture evidence.' }
} finally {
    Pop-Location
    if (Test-Path -LiteralPath $PreviousNativeDirectory) { [Environment]::CurrentDirectory=$PreviousNativeDirectory }
    $env:PYTHONPATH=$PreviousPythonPath
}
