"""R2: the elevated start shortcut runs the private venv Python isolated (-I -B)."""

import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/windows/Start-ReleaseTester.ps1"


def _bootstrap():
    source = SCRIPT.read_text(encoding="utf-8-sig")
    assert "& $Python -I -B -c $Bootstrap $Release start --root $Root" in source
    assert " -m src.api.release_service" not in source
    match = re.search(r'^\s*\$Bootstrap="([^"]+)"\s*$', source, re.MULTILINE)
    assert match, "bootstrap literal must not contain embedded double quotes (PS 5.1 args)"
    return match.group(1)


def _tree(base, label):
    package = base / "src/api"
    package.mkdir(parents=True)
    (base / "src/__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "release_service.py").write_text(
        f"import sys\nprint('{label}', sys.argv[1:], sys.flags.isolated, sys.dont_write_bytecode)\n"
    )


def test_injected_pythonpath_cannot_hijack_elevated_start(tmp_path):
    release, evil = tmp_path / "release", tmp_path / "evil"
    _tree(release, "GENUINE")
    _tree(evil, "INJECTED")
    (evil / "sitecustomize.py").write_text("print('SITECUSTOMIZE-INJECTED')\n")
    env = dict(os.environ, PYTHONPATH=str(evil), PYTHONSTARTUP=str(evil / "sitecustomize.py"))
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", _bootstrap(), str(release), "start", "--root", "R"],
        cwd=evil,  # Even a hostile working directory is not on sys.path.
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    assert "INJECTED" not in result.stdout + result.stderr
    assert result.stdout.strip() == "GENUINE ['start', '--root', 'R'] 1 True"
    # The pre-fix invocation (no -I) executes an attacker sitecustomize from PYTHONPATH.
    legacy = subprocess.run(
        [sys.executable, "-B", "-m", "src.api.release_service", "start", "--root", "R"],
        cwd=release,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    assert "SITECUSTOMIZE-INJECTED" in legacy.stdout
