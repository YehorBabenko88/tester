"""Acceptance child isolation; real Windows PowerShell cmdlets, no ACL mutation."""

import os
import shutil
import subprocess
import sys

import pytest

from scripts.acceptance import check


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell process boundary")
def test_acceptance_child_rebuilds_edition_specific_module_path(monkeypatch, tmp_path):
    pwsh = shutil.which("pwsh")
    if pwsh:
        inherited = subprocess.run(
            [
                pwsh,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "[Console]::WriteLine($env:PSModulePath)",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    else:
        # An explicitly inherited path without PS5 built-ins reproduces the same
        # boundary on hosts that support PS5 but have no PS7 installed.
        inherited = str(tmp_path / "foreign-edition-modules")
    monkeypatch.setenv("PSModulePath", inherited)
    monkeypatch.setenv("TESTER_MODULE_ENV_SENTINEL", "preserved")
    probe = r"""
import os,subprocess
assert 'PSModulePath' not in os.environ, 'foreign edition module path leaked to acceptance child'
assert os.environ['TESTER_MODULE_ENV_SENTINEL']=='preserved'
result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',
    "$ErrorActionPreference='Stop';if ($PSVersionTable.PSVersion.Major -ne 5) {throw 'Expected real Windows PowerShell 5'};"
    "Import-Module Microsoft.PowerShell.Security;Import-Module Microsoft.PowerShell.Utility;"
    "if ((Get-Command Get-Acl).Source -ne 'Microsoft.PowerShell.Security') {throw 'ACL cmdlet absent'};"
    "if ((Get-Command Get-FileHash).Source -ne 'Microsoft.PowerShell.Utility') {throw 'Hash cmdlet absent'}"],
    capture_output=True,text=True,timeout=10)
assert result.returncode==0,result.stderr
"""
    check("-c", probe)
    assert os.environ["PSModulePath"] == inherited, "parent/host environment must remain unchanged"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell process boundary")
def test_direct_pytest_uses_real_ps5_builtin_modules():
    assert "PSModulePath" not in os.environ, "pytest must not forward a foreign edition search path"
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$ErrorActionPreference='Stop';if ($PSVersionTable.PSVersion.Major -ne 5) {throw 'Expected PS5'};"
            "Import-Module Microsoft.PowerShell.Security;Import-Module Microsoft.PowerShell.Utility;"
            "Get-Command Get-Acl,Get-FileHash | Out-Null",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
