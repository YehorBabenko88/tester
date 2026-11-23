import hashlib
import json
import subprocess

import pytest

from scripts.build_release import archive, build, component_files


def fixture(tmp_path):
    repo, components = tmp_path / "repo", tmp_path / "components"
    repo.mkdir()
    files = {
        "requirements-release-windows312.lock": "x==1 --hash=sha256:"
        + hashlib.sha256(b"wheel").hexdigest(),
        "scripts/windows/Setup-Tester.ps1": "verified bootstrap",
        "src/api/release_install.py": "installer",
        "src/api/release_security.py": "security",
        "frontend/index.html": "<html>terminal</html>",
        "scripts/windows/Start-ReleaseTester.ps1": "start",
        "scripts/windows/Uninstall-ReleaseTester.ps1": "remove",
        "docs/evidence/accepted.json": "old acceptance SHA preserved",
    }
    for name, value in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value)
    for args in [
        ("init",),
        ("config", "user.name", "Test"),
        ("config", "user.email", "test@example.invalid"),
        ("add", "."),
        ("commit", "-m", "fixture"),
    ]:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"]).decode().strip()
    names = (
        [
            "runtimes/python/python.exe",
            "runtimes/python/Lib/venv/__init__.py",
            "runtimes/python/Lib/site-packages/cryptography/__init__.py",
            "runtimes/postgres/share/postgres.bki",
        ]
        + [
            f"runtimes/postgres/bin/{n}.exe"
            for n in ("initdb", "pg_ctl", "pg_controldata", "postgres")
        ]
        + ["wheelhouse/x-1-py3-none-any.whl"]
    )
    for name in names:
        p = components / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"wheel" if name.startswith("wheelhouse") else b"fixture-only")
    pins = tmp_path / "pins.json"
    pins.write_text(
        json.dumps(
            {
                "schema": "tester-components/v1",
                "python_version": "3.12.10",
                "architecture": "win_amd64",
                "files": component_files(components),
            }
        )
    )
    return repo, components, pins, sha


def test_immutable_source_reproducible_manifest_and_archive(tmp_path):
    repo, components, pins, sha = fixture(tmp_path)
    (repo / "frontend/index.html").write_text("dirty secret")
    first, second = tmp_path / "one", tmp_path / "two"
    a = build(repo, sha, components, pins, first)
    b = build(repo, sha, components, pins, second)
    assert a == b
    assert (first / "payload/frontend/index.html").read_text() == "<html>terminal</html>"
    assert not (first / "release-manifest.json").exists()
    assert (
        json.loads((tmp_path / "one.status.json").read_text())["production_status"]
        == "BLOCKED_UNSIGNED"
    )
    assert archive(first, tmp_path / "one.zip") == archive(second, tmp_path / "two.zip")
    assert a["files"] == component_files(first)
    import zipfile

    with zipfile.ZipFile(first / "source-evidence.zip") as z:
        assert z.read("docs/evidence/accepted.json") == b"old acceptance SHA preserved"


def test_tampered_component_and_extra_wheel_fail_before_output(tmp_path):
    repo, components, pins, sha = fixture(tmp_path)
    (components / "runtimes/python/python.exe").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash pins"):
        build(repo, sha, components, pins, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_missing_canonical_source_fails(tmp_path):
    repo, components, pins, sha = fixture(tmp_path)
    subprocess.run(
        ["git", "-C", str(repo), "rm", "scripts/windows/Setup-Tester.ps1"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "missing"], check=True, capture_output=True
    )
    sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"]).decode().strip()
    with pytest.raises(ValueError, match="Canonical source missing"):
        build(repo, sha, components, pins, tmp_path / "out")
