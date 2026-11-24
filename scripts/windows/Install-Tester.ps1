[CmdletBinding()]
param(
    [string]$Python = 'python',
    [string]$Database = 'tester_local',
    [string]$Role = 'tester_local',
    [int]$Port = 8765,
    [long]$MinFreeBytes = 1GB,
    [switch]$NoStart
)
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Set-Location -LiteralPath $repo
& $Python -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required' }
$runtime = Join-Path $repo 'runtime'
if (Test-Path -LiteralPath (Join-Path $runtime 'config.json')) { throw 'Already installed; use update procedure' }
if ((Get-Item -LiteralPath $repo).PSDrive.Free -lt $MinFreeBytes) { throw 'Insufficient disk space' }
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
if ((Get-Item -LiteralPath $runtime).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Runtime cannot be a link' }
# Apply restrictive inherited ACL BEFORE writing a password-bearing configuration.
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().User
$acl = New-Object Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true, $false)
$acl.SetOwner($identity)
$inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
$propagation = [Security.AccessControl.PropagationFlags]::None
foreach ($sid in @($identity, [Security.Principal.SecurityIdentifier]'S-1-5-18')) {
    $rule = New-Object Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', $inherit, $propagation, 'Allow')
    $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $runtime -AclObject $acl
& $Python -m venv (Join-Path $repo '.venv')
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
$venvPython = Join-Path $repo '.venv/Scripts/python.exe'
& $venvPython -m pip install --disable-pip-version-check 'pip>=26.2,<27'
if ($LASTEXITCODE -ne 0) { throw 'Secure pip upgrade failed' }
& $venvPython -m pip install --disable-pip-version-check -e . -c requirements-platform.lock
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& $venvPython -m src.api.bootstrap --data-dir $runtime --database $Database --role $Role --port $Port --min-free-bytes $MinFreeBytes
if ($LASTEXITCODE -ne 0) { throw 'Database setup failed; see docs/operations.md' }
if (-not $NoStart) { & (Join-Path $PSScriptRoot 'Start-Tester.ps1') }
