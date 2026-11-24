import hashlib
import json
import subprocess

import pytest

from scripts.build_release import archive, build, component_files


def fixture(tmp_path, version="3.14.10"):
    repo, components = tmp_path / "repo", tmp_path / "components"
    repo.mkdir()
    lock = "requirements-release-windows" + version.replace(".", "")[:3] + ".lock"
    files = {
        lock: "x==1 --hash=sha256:" + hashlib.sha256(b"wheel").hexdigest(),
        "scripts/windows/Setup-Tester.ps1": "verified bootstrap",
        "src/api/release_install.py": "installer uses " + lock,
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
                "python_version": version,
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


@pytest.mark.parametrize("mutation", ["tamper", "extra-wheel", "hardlink"])
def test_tampered_component_and_extra_wheel_fail_before_output(tmp_path, mutation):
    repo, components, pins, sha = fixture(tmp_path)
    if mutation == "tamper":
        (components / "runtimes/python/python.exe").write_bytes(b"tampered")
    elif mutation == "extra-wheel":
        (components / "wheelhouse/unpinned.whl").write_bytes(b"untrusted")
    else:
        import os

        os.link(components / "runtimes/python/python.exe", tmp_path / "alias.exe")
    with pytest.raises(ValueError):
        build(repo, sha, components, pins, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("version", ["3.12.10", "3.13.9", "3.15.0"])
def test_only_shipped_installer_abi_is_buildable_issue112(tmp_path, version):
    # The installer hard-codes the 3.14 lock; a 3.12 build was accepted here
    # and then always failed later with an ABI/lock mismatch.
    repo, components, pins, sha = fixture(tmp_path, version)
    with pytest.raises(ValueError, match="Pinned supported Windows Python"):
        build(repo, sha, components, pins, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_release_lock_matches_installer_and_ci_issue112():
    from pathlib import Path

    from scripts.build_release import RELEASE_LOCK, SOURCE_FILES, SUPPORTED_PYTHON

    root = Path(__file__).resolve().parents[2]
    assert SUPPORTED_PYTHON == "3.14.10"
    assert RELEASE_LOCK in SOURCE_FILES
    assert (root / RELEASE_LOCK).is_file()
    locks = sorted(p.name for p in root.glob("requirements-release-windows*.lock"))
    assert locks == [RELEASE_LOCK]
    assert not [n for n in SOURCE_FILES if n.startswith("requirements-") and n != RELEASE_LOCK]
    for consumer in ("src/api/release_install.py", "src/api/release_provision.py"):
        text = (root / consumer).read_text(encoding="utf-8")
        assert RELEASE_LOCK in text
        assert "windows312" not in text
    workflow = (root / ".github/workflows/release-candidate.yml").read_text(encoding="utf-8")
    assert "-r " + RELEASE_LOCK in workflow
    ci = (root / "scripts/release_ci_components.py").read_text(encoding="utf-8")
    assert SUPPORTED_PYTHON in ci and RELEASE_LOCK in ci


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


def frozen_overlay(tmp_path, repo, sha, publisher="a" * 64):
    from scripts.build_release import SIGNED_SCRIPT_PATHS

    overlay = tmp_path / "frozen-overlay"
    rows = []
    for name in sorted(SIGNED_SCRIPT_PATHS):
        source = (
            "scripts/windows/Setup-Tester.ps1"
            if name == "Setup-Tester.ps1"
            else name.removeprefix("payload/")
        )
        before = subprocess.check_output(["git", "-C", str(repo), "show", sha + ":" + source])
        after = (
            before
            + b"\n# SIG # Begin signature block\n# FIXTURE_ONLY_NOT_AUTHENTICODE\n# SIG # End signature block\n"
        )
        path = overlay / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(after)
        rows.append(
            dict(
                path=name,
                source_sha256=hashlib.sha256(before).hexdigest(),
                signed_sha256=hashlib.sha256(after).hexdigest(),
                certificate_sha256=publisher,
            )
        )
    (overlay / "script-signing-receipt.json").write_text(
        json.dumps(dict(schema="tester-script-signatures/v1", source_sha=sha, scripts=rows))
    )
    return overlay


@pytest.fixture
def frozen_fixture_handles(monkeypatch):
    import contextlib
    from types import SimpleNamespace

    from scripts import build_release as release

    @contextlib.contextmanager
    def held_fixture(root, names):
        held = {}
        for name in names:
            data = (root / name).read_bytes()
            held[name] = SimpleNamespace(
                read_bytes=lambda *, max_bytes, data=data: data,
                digest=lambda data=data: hashlib.sha256(data).hexdigest(),
            )
        yield held

    monkeypatch.setattr(release, "overlay_handles", held_fixture)


def test_frozen_fixture_overlay_two_clean_builds_are_identical(
    tmp_path, monkeypatch, frozen_fixture_handles
):
    from scripts import build_release as release

    repo, components, pins, sha = fixture(tmp_path)
    overlay = frozen_overlay(tmp_path, repo, sha)
    checked = []
    # Pure unit fixture only: no production certificate or Authenticode claim.
    monkeypatch.setattr(
        release, "verify_overlay_publisher", lambda path, pin: checked.append((path.name, pin))
    )
    first, second = tmp_path / "signed-one", tmp_path / "signed-two"
    a = build(
        repo,
        sha,
        components,
        pins,
        first,
        signed_overlay=overlay,
        trusted_publisher_sha256="a" * 64,
    )
    b = build(
        repo,
        sha,
        components,
        pins,
        second,
        signed_overlay=overlay,
        trusted_publisher_sha256="a" * 64,
    )
    assert a == b and len(checked) == 6
    assert "script-signing-receipt.json" in a["files"]
    assert archive(first, tmp_path / "signed-one.zip") == archive(
        second, tmp_path / "signed-two.zip"
    )
    assert (
        json.loads((tmp_path / "signed-one.status.json").read_text())["production_status"]
        == "BLOCKED_UNSIGNED"
    )


@pytest.mark.parametrize(
    "mutation", ["source", "executable-tail", "non-ascii-tail", "certificate", "duplicate", "extra"]
)
def test_overlay_refuses_unapproved_source_or_inventory(
    tmp_path, monkeypatch, mutation, frozen_fixture_handles
):
    from scripts import build_release as release

    repo, components, pins, sha = fixture(tmp_path)
    overlay = frozen_overlay(tmp_path, repo, sha)
    receipt_path = overlay / "script-signing-receipt.json"
    receipt = json.loads(receipt_path.read_text())
    row = receipt["scripts"][0]
    path = overlay / row["path"]
    if mutation == "source":
        path.write_bytes(b"changed executable body\n" + path.read_bytes())
        row["signed_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    elif mutation == "executable-tail":
        path.write_bytes(path.read_bytes() + b"Write-Output dangerous\n")
        row["signed_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    elif mutation == "non-ascii-tail":
        path.write_bytes(path.read_bytes().replace(b"# FIXTURE_ONLY", b"# \xe2\x80\xa8EXECUTABLE"))
        row["signed_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    elif mutation == "certificate":
        row["certificate_sha256"] = "b" * 64
    elif mutation == "duplicate":
        receipt["scripts"].append(row.copy())
    else:
        (overlay / "unexpected.ps1").write_bytes(b"unreviewed")
    receipt_path.write_text(json.dumps(receipt))
    monkeypatch.setattr(release, "verify_overlay_publisher", lambda *args: None)
    with pytest.raises(ValueError):
        build(
            repo,
            sha,
            components,
            pins,
            tmp_path / "rejected",
            signed_overlay=overlay,
            trusted_publisher_sha256="a" * 64,
        )
    assert not (tmp_path / "rejected").exists()


def test_native_overlay_reuses_pinned_security_handles_top_down(tmp_path, monkeypatch):
    import os
    import sys
    from types import SimpleNamespace

    from scripts import build_release as release

    root = tmp_path / "protected-overlay"
    leaf = root / "nested/script.ps1"
    leaf.parent.mkdir(parents=True)
    leaf.write_bytes(b"fixture")
    opened, closed = [], []

    class NativeFixture:
        def __init__(self, path, *, delete=False):
            assert not delete
            self.path = path
            self.is_dir = path.is_dir()
            opened.append(self)

        def close(self):
            closed.append(self)

    checks = []
    monkeypatch.setattr(release, "os", SimpleNamespace(name="nt", environ=os.environ))
    monkeypatch.setitem(
        sys.modules,
        "src.api.release_uninstall",
        SimpleNamespace(
            _WindowsObject=NativeFixture,
            _security_record=lambda obj: obj.path,
            _validate_admin_security=lambda record: checks.append(record),
        ),
    )
    with release.overlay_handles(root, ["nested/script.ps1"]) as held:
        assert held["nested/script.ps1"] is opened[-1]
        assert not closed
        directories = opened[:-1]
        assert [len(obj.path.parts) for obj in directories] == sorted(
            len(obj.path.parts) for obj in directories
        )
        assert root in checks and leaf in checks and leaf.parent in checks
    assert closed == opened[::-1]


def test_overlay_copies_captured_verified_bytes_without_path_reread(
    tmp_path, monkeypatch, frozen_fixture_handles
):
    from scripts import build_release as release

    repo, components, pins, sha = fixture(tmp_path)
    overlay = frozen_overlay(tmp_path, repo, sha)
    approved = (overlay / "Setup-Tester.ps1").read_bytes()

    def fixture_verifier(path, pin):
        # Real native leases refuse this write; fake fixture leases preserve captured bytes.
        path.write_bytes(b"unapproved path replacement")

    monkeypatch.setattr(release, "verify_overlay_publisher", fixture_verifier)
    output = tmp_path / "captured-output"
    build(
        repo,
        sha,
        components,
        pins,
        output,
        signed_overlay=overlay,
        trusted_publisher_sha256="a" * 64,
    )
    assert (output / "Setup-Tester.ps1").read_bytes() == approved


def test_overlay_verifier_uses_kernel_resolved_powershell_not_path_or_systemroot(
    tmp_path, monkeypatch
):
    import os
    import sys
    from types import SimpleNamespace

    from scripts import build_release as release

    trusted = tmp_path / "kernel-system/WindowsPowerShell/v1.0/powershell.exe"
    malicious = tmp_path / "untrusted"
    monkeypatch.setenv("PATH", str(malicious))
    monkeypatch.setenv("SystemRoot", str(malicious))
    monkeypatch.setenv("PSModulePath", "untrusted-powershell-modules")
    monkeypatch.setattr(release, "os", SimpleNamespace(name="nt", environ=os.environ))

    def kernel_resolver(relative):
        assert relative == "WindowsPowerShell/v1.0/powershell.exe"
        return trusted

    monkeypatch.setitem(
        sys.modules, "src.api.release_install", SimpleNamespace(system_binary=kernel_resolver)
    )
    observed = []

    def checked_run(args, **kwargs):
        observed.append(args)
        assert args[0] == str(trusted)
        assert kwargs["check"] is True
        assert not any(k.upper() == "PSMODULEPATH" for k in kwargs["env"])

    monkeypatch.setattr(release.subprocess, "run", checked_run)
    release.verify_overlay_publisher(tmp_path / "signed.ps1", "a" * 64)
    assert len(observed) == 1


def test_archive_refuses_mutation_between_inventory_and_read(tmp_path, monkeypatch):
    from scripts import build_release as release

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    payload = bundle / "payload.txt"
    payload.write_bytes(b"approved")
    original = release.component_files
    first = True

    def inventory_then_mutate(root):
        nonlocal first
        result = original(root)
        if first:
            first = False
            payload.write_bytes(b"unapproved replacement")
        return result

    monkeypatch.setattr(release, "component_files", inventory_then_mutate)
    destination = tmp_path / "release.zip"
    with pytest.raises(ValueError, match="changed|digest|inventory"):
        archive(bundle, destination)
    assert not destination.exists()


def test_archive_tiny_unsigned_snapshot_is_deterministic(tmp_path):
    import zipfile

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "b.txt").write_bytes(b"beta")
    (bundle / "a.txt").write_bytes(b"alpha")
    one, two = tmp_path / "one.zip", tmp_path / "two.zip"
    assert archive(bundle, one) == archive(bundle, two)
    assert one.read_bytes() == two.read_bytes()
    with zipfile.ZipFile(one) as z:
        assert z.namelist() == ["a.txt", "b.txt"]
        assert z.read("a.txt") == b"alpha"


def test_archive_refuses_extra_file_after_inventory(tmp_path, monkeypatch):
    from scripts import build_release as release

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "a.txt").write_bytes(b"approved")
    original = release.component_files
    first = True

    def inventory_then_add(root):
        nonlocal first
        result = original(root)
        if first:
            first = False
            (bundle / "unexpected.txt").write_bytes(b"not-approved")
        return result

    monkeypatch.setattr(release, "component_files", inventory_then_add)
    with pytest.raises(ValueError, match="tree|inventory"):
        archive(bundle, tmp_path / "release.zip")
    assert not (tmp_path / "release.zip").exists()
    assert not list(tmp_path.glob(".tester-archive-*"))


def test_archive_refuses_bundle_destination_and_preserves_foreign_output(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "a.txt").write_bytes(b"approved")
    with pytest.raises(ValueError, match="outside"):
        archive(bundle, bundle / "inside.zip")
    foreign = tmp_path / "foreign.zip"
    foreign.write_bytes(b"foreign sentinel")
    with pytest.raises(FileExistsError):
        archive(bundle, foreign)
    assert foreign.read_bytes() == b"foreign sentinel"


def test_archive_signed_bundle_requires_verified_complete_map(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "a.txt").write_bytes(b"approved")
    envelope = dict(manifest=dict(files=component_files(bundle)))
    (bundle / "release-manifest.json").write_text(json.dumps(envelope))
    with pytest.raises(ValueError, match="independently verified"):
        archive(bundle, tmp_path / "unapproved.zip")
    approved = component_files(bundle)
    assert archive(bundle, tmp_path / "signed.zip", expected_inventory=approved)
    (bundle / "a.txt").write_bytes(b"changed-before-snapshot")
    with pytest.raises(ValueError, match="approved"):
        archive(bundle, tmp_path / "changed.zip", expected_inventory=approved)
    assert not (tmp_path / "changed.zip").exists()


def test_archive_publication_race_never_deletes_foreign_destination(tmp_path, monkeypatch):
    from scripts import build_release as release

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "a.txt").write_bytes(b"approved")
    destination = tmp_path / "release.zip"

    def foreign_wins(source, target):
        destination.write_bytes(b"foreign winner")
        raise FileExistsError(target)

    monkeypatch.setattr(release.os, "link", foreign_wins)
    with pytest.raises(FileExistsError):
        archive(bundle, destination)
    assert destination.read_bytes() == b"foreign winner"
    assert not list(tmp_path.glob(".tester-archive-*"))


def test_python_bounds_cover_release_runtime_and_ci_matrix_issue112():
    import re
    import tomllib
    from pathlib import Path

    from scripts.build_release import SUPPORTED_PYTHON

    root = Path(__file__).resolve().parents[2]
    spec = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "requires-python"
    ]
    # Explicit upper bound: only CPython ABIs exercised by CI and the shipped
    # 3.14 wheel lock are declared supported.
    assert spec == ">=3.11,<3.15"

    def minor(version):
        return tuple(int(x) for x in version.split(".")[:2])

    versions = {SUPPORTED_PYTHON}
    for workflow in (root / ".github/workflows").glob("*.yml"):
        text = workflow.read_text(encoding="utf-8")
        versions.update(re.findall(r"python-version:\s*['\"]([0-9.]+)['\"]", text))
        for listed in re.findall(r"\bpython:\s*\[([^\]]*)\]", text):
            versions.update(re.findall(r"['\"]([0-9.]+)['\"]", listed))
    assert versions
    assert all((3, 11) <= minor(v) < (3, 15) for v in versions), sorted(versions)
    assert {minor(v) for v in versions} == {(3, 11), (3, 12), (3, 13), (3, 14)}
    lab = (root / "requirements-windows-lab.lock").read_text(encoding="utf-8")
    assert lab.startswith("# CPython 3.14 Windows x64")
