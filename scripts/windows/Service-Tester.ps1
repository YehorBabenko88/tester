# Intentionally plan-only. No -Execute switch and no SCM/registry mutations.
[CmdletBinding()]
param([ValidateSet('install','repair','uninstall')][string]$Action = 'install')
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$python = Join-Path $repo '.venv/Scripts/python.exe'
Set-Location -LiteralPath $repo
& $python -m src.api.scm_plan $Action --repo $repo
if ($LASTEXITCODE -ne 0) { throw 'SCM dry-run generation failed' }
