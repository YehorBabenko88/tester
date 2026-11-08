"""Real native loader probe; never registers or controls an SCM service."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native service host")
def test_private_native_host_loads_manager_before_service_pythonpath(tmp_path):
    source = os.environ.get("TESTER_NATIVE_RUNTIME_SOURCE")
    if not source:
        pytest.skip("Set TESTER_NATIVE_RUNTIME_SOURCE to a staged private service runtime")
    source = Path(source)
    assert (source / "servicemanager.pyd").is_file(), "stage the bootstrap module beside the host"
    runtime = tmp_path / "private-runtime"
    shutil.copytree(source, runtime)
    pth = runtime / "Lib/site-packages/tester-lab.pth"
    paths = pth.read_text(encoding="utf-8").splitlines()
    assert len(paths) == 5
    assert all(not line.startswith("import ") for line in paths)
    paths[0] = str(runtime)
    # Prove the early native import in the absence of later package search paths.
    pth.unlink()
    manager = runtime / "servicemanager.pyd"
    module_bytes = manager.read_bytes()
    manager.unlink()
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
    before = subprocess.run(
        [str(runtime / "pythonservice.exe")], cwd=tmp_path, env=env, capture_output=True, timeout=15
    )
    assert before.returncode == 1
    assert b"unable to locate the service manager" in before.stderr
    manager.write_bytes(module_bytes)
    after = subprocess.run(
        [str(runtime / "pythonservice.exe")], cwd=tmp_path, env=env, capture_output=True, timeout=15
    )
    assert after.returncode == 2  # native host's no-arguments usage branch
    assert b"unable to locate the service manager" not in after.stderr
    assert b"Options:" in after.stdout
    pth.write_text("\n".join(paths) + "\n", encoding="utf-8")
    imports = subprocess.run(
        [
            str(runtime / "python.exe"),
            "-I",
            "-c",
            "import sys,lab_service_bootstrap,win32serviceutil; "
            "assert sys.dont_write_bytecode; print('private-path-bootstrap:OK')",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=15,
    )
    assert imports.returncode == 0, (imports.stdout, imports.stderr)
    assert b"private-path-bootstrap:OK" in imports.stdout
    probe = """
import servicemanager
servicemanager.Initialize('TesterNativeLoaderProbe', '')
servicemanager.PrepareToHostSingle(type('Probe', (), {'_svc_name_': 'TesterNativeLoaderProbe'}))
try:
    servicemanager.StartServiceCtrlDispatcher()
except Exception as exc:
    assert exc.winerror == 1063, repr(exc)
    print('native-dispatcher-outside-SCM:1063')
else:
    raise AssertionError('Unexpected SCM connection')
"""
    dispatcher = subprocess.run(
        [str(runtime / "python.exe"), "-I", "-c", probe],
        cwd=runtime,
        env=env,
        capture_output=True,
        timeout=15,
    )
    assert dispatcher.returncode == 0, (dispatcher.stdout, dispatcher.stderr)
    assert b"native-dispatcher-outside-SCM:1063" in dispatcher.stdout
