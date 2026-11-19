#requires -Version 5.1
<#
  Disposable Windows fixture ONLY. No real desktop, services or data effects.
  The production shortcut adapter needs signed release/OS attestation and GO.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$FixtureDesktop,
    [switch]$CreateFixture
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $CreateFixture) { throw 'Fixture-only script requires -CreateFixture' }
$TempRoot = [IO.Path]::GetFullPath($env:RUNNER_TEMP)
$Target = [IO.Path]::GetFullPath($FixtureDesktop)
if (-not $Target.StartsWith($TempRoot.TrimEnd('\\') + '\\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Refusing non-fixture desktop path'
}
if (-not (Test-Path -LiteralPath $Target -PathType Container)) {
    throw 'Fixture desktop must already exist'
}
$Cursor = Get-Item -LiteralPath $Target -Force
while ($null -ne $Cursor) {
    if (($Cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'Reparse fixture desktop or ancestor refused'
    }
    $Cursor = $Cursor.Parent
}
$Names = @('TESTER — Запуск','TESTER — Удаление')
$Scripts = @('Start-Tester.ps1','Uninstall-Tester.ps1')
$Shell = New-Object -ComObject WScript.Shell
for ($Index = 0; $Index -lt 2; $Index++) {
    $Path = Join-Path $Target ($Names[$Index] + '.lnk')
    if (Test-Path -LiteralPath $Path) {
        $Old = Get-Item -LiteralPath $Path -Force
        if (($Old.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'Reparse shortcut refused'
        }
        $Current = $Shell.CreateShortcut($Path)
        if (
            $Current.Arguments -notlike ('*' + $Scripts[$Index] + '*') -or
            $Current.TargetPath -ne (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -or
            $Current.WorkingDirectory -ne 'C:\TesterLab'
        ) {
            throw 'Foreign shortcut collision'
        }
    }
    $Shortcut = $Shell.CreateShortcut($Path)
    $Shortcut.TargetPath = (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe')
    $Shortcut.Arguments = '-NoProfile -ExecutionPolicy AllSigned -File "C:\TesterLab\scripts\' + $Scripts[$Index] + '"'
    $Shortcut.WorkingDirectory = 'C:\TesterLab'
    $Shortcut.Description = 'TESTER signed pinned release — fixture only'
    $Shortcut.Save()
}
if (@(Get-ChildItem -LiteralPath $Target -Filter 'TESTER — *.lnk').Count -ne 2) {
    throw 'Expected exactly two TESTER shortcuts'
}
Write-Output 'FIXTURE_SHORTCUTS_PASS'
