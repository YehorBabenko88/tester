"""Scope Windows built-in module resolution to the platform test process only."""

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def windows_powershell_default_modules():
    # CI's pwsh 7 parent inserts its own Security/Utility modules in PSModulePath.
    # Our real Windows PowerShell 5 subprocesses must build PS5 defaults instead.
    # No command, ACL, guard, assertion or other test environment is replaced.
    # This also covers direct pytest invocations outside scripts/acceptance.py.
    if os.name != "nt":
        yield
        return
    original = os.environ.pop("PSModulePath", None)
    try:
        yield
    finally:
        if original is not None:
            os.environ["PSModulePath"] = original
