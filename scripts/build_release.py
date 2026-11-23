"""Deterministic offline release staging from immutable Git blobs and pinned components."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

REPOSITORY = "https://github.com/YehorBabenko88/tester"
SOURCE_ROOTS = ("src/", "contracts/", "frontend/")
SOURCE_FILES = {
    "scripts/windows/Start-ReleaseTester.ps1",
    "scripts/windows/Uninstall-ReleaseTester.ps1",
    "pyproject.toml",
    "requirements-release-windows312.lock",
    "requirements-release-windows314.lock",
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_name(value):
    p = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or ":" in value
        or p.is_absolute()
        or any(x in {"", ".", ".."} for x in value.split("/"))
    ):
        raise ValueError("Unsafe component path")
    return p


def regular(path):
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or getattr(info, "st_file_attributes", 0) & 0x400
    ):
        raise ValueError(f"Component is not a single-link regular file: {path}")


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def component_files(root):
    result = {}
    for p in sorted(root.rglob("*")):
        info = p.lstat()
        if p.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Linked component rejected")
        if p.is_file():
            regular(p)
            result[p.relative_to(root).as_posix()] = digest(p)
    return result


def write_json(path, value):
    path.write_bytes(canonical(value) + b"\n")


def build(repo, source_sha, components, pins_path, output):
    repo, components, output = Path(repo), Path(components), Path(output)
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("Exact forty character SHA required")
    if git(repo, "rev-parse", source_sha + "^{commit}").decode().strip() != source_sha:
        raise ValueError("Source commit mismatch")
    if output.exists():
        raise ValueError("Output must not exist")
    pins = json.loads(Path(pins_path).read_text(encoding="utf-8-sig"))
    if (
        pins.get("schema") != "tester-components/v1"
        or pins.get("python_version") not in {"3.12.10", "3.14.10"}
        or pins.get("architecture") != "win_amd64"
    ):
        raise ValueError("Pinned supported Windows Python component contract required")
    lock_name = (
        "requirements-release-windows"
        + ("314" if pins["python_version"] == "3.14.10" else "312")
        + ".lock"
    )
    expected = pins.get("files", {})
    for name, value in expected.items():
        safe_name(name)
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("Invalid component digest")
        if not name.startswith(("runtimes/python/", "runtimes/postgres/", "wheelhouse/")):
            raise ValueError("Component outside supported roots")
    if not expected or component_files(components) != expected:
        raise ValueError("Components differ from independently reviewed hash pins")
    required = [
        "runtimes/python/python.exe",
        "runtimes/python/Lib/venv/__init__.py",
        "runtimes/python/Lib/site-packages/cryptography/__init__.py",
        "runtimes/postgres/share/postgres.bki",
    ]
    required += [
        f"runtimes/postgres/bin/{name}.exe"
        for name in ("initdb", "pg_ctl", "pg_controldata", "postgres")
    ]
    if not all(name in expected for name in required):
        raise ValueError(
            "Complete private Python, bootstrap cryptography and dedicated PostgreSQL required"
        )
    entries = git(repo, "ls-tree", "-rz", "--full-tree", source_sha).split(b"\0")
    sources = {}
    evidence = {}
    for entry in filter(None, entries):
        header, raw = entry.split(b"\t", 1)
        mode, kind, oid = header.split()
        name = raw.decode()
        safe_name(name)
        if kind != b"blob" or mode not in (b"100644", b"100755"):
            raise ValueError("Nonregular tracked source rejected")
        if Path(name).name in {".env", "id_rsa", "id_ed25519"} or name.lower().endswith(
            (".pem", ".pfx", ".key")
        ):
            raise ValueError("Potential secret in tracked source requires review")
        evidence[name] = git(repo, "cat-file", "blob", oid.decode())
        if (
            name in SOURCE_FILES
            or name.startswith(SOURCE_ROOTS)
            or name == "scripts/windows/Setup-Tester.ps1"
        ):
            safe_name(name)
            if kind != b"blob" or mode not in (b"100644", b"100755"):
                raise ValueError("Linked or submodule source rejected")
            sources[name] = git(repo, "cat-file", "blob", oid.decode())
    for name in (
        lock_name,
        "scripts/windows/Setup-Tester.ps1",
        "src/api/release_install.py",
        "src/api/release_security.py",
        "frontend/index.html",
        "scripts/windows/Start-ReleaseTester.ps1",
        "scripts/windows/Uninstall-ReleaseTester.ps1",
    ):
        if name not in sources:
            raise ValueError(f"Canonical source missing: {name}")
    lock = sources[lock_name].decode()
    allowed = set(re.findall(r"--hash=sha256:([0-9a-f]{64})", lock))
    wheels = {name: value for name, value in expected.items() if name.startswith("wheelhouse/")}
    if (
        not allowed
        or set(wheels.values()) != allowed
        or any(not n.endswith(".whl") for n in wheels)
    ):
        raise ValueError("Wheelhouse must match full actual-wheel lock exactly")
    output.mkdir(parents=True)
    for name, data in sources.items():
        target = (
            "Setup-Tester.ps1" if name == "scripts/windows/Setup-Tester.ps1" else "payload/" + name
        )
        p = output / target
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    with zipfile.ZipFile(output / "source-evidence.zip", "x", compression=zipfile.ZIP_STORED) as z:
        for name, data in sorted(evidence.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            z.writestr(info, data)
    for name in expected:
        p = output / name
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(components / name, p)
        if digest(p) != expected[name]:
            raise ValueError("Component changed while copying")
    write_json(output / "component-inventory.json", pins)
    write_json(
        output / "sbom.json",
        {
            "schema": "tester-sbom/v1",
            "source_sha": source_sha,
            "python": pins["python_version"],
            "architecture": "win_amd64",
            "components": [{"path": n, "sha256": h} for n, h in sorted(expected.items())],
            "dependency_lock": "payload/" + lock_name,
        },
    )
    (output / "release-notes.md").write_text(
        f"# TESTER release candidate\n\nSource: {source_sha}\n\nGUARDIANS_ENABLED=false. Public market data only.\nProduction signing and independent Windows lifecycle acceptance required.\n",
        encoding="utf-8",
    )
    manifest = {
        "schema": "tester-release/v1",
        "release_version": "1",
        "source_sha": source_sha,
        "repository": REPOSITORY,
        "files": component_files(output),
    }
    unsigned = output.with_name(output.name + ".unsigned.json")
    if unsigned.exists():
        raise ValueError("Unsigned manifest output already exists")
    write_json(unsigned, manifest)
    write_json(
        output.with_name(output.name + ".status.json"),
        {
            "production_status": "BLOCKED_UNSIGNED",
            "source_sha": source_sha,
            "unsigned_manifest_sha256": digest(unsigned),
            "next_action": "Owner signs using external production Ed25519 key; distribute trusted public key hash and envelope hash independently.",
        },
    )
    return manifest


def archive(bundle, destination):
    """Stable ZIP bytes: sorted paths, fixed timestamps and permissions, no platform metadata."""
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_STORED) as z:
        for name in component_files(Path(bundle)):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            z.writestr(info, (Path(bundle) / name).read_bytes())
    return digest(Path(destination))


def stage_local_components(python_root, postgres_root, wheelhouse, lock, output, pins_path):
    """Copy explicit read-only runtime allowlists; resulting pins require independent review."""

    python_root, postgres_root, wheelhouse, output = map(
        Path, (python_root, postgres_root, wheelhouse, output)
    )
    if output.exists() or Path(pins_path).exists():
        raise ValueError("Fresh component staging and pin paths required")
    version = (
        subprocess.check_output(
            [
                str(python_root / "python.exe"),
                "-I",
                "-B",
                "-c",
                "import platform;print(platform.python_version())",
            ]
        )
        .decode()
        .strip()
    )
    if version not in {"3.12.10", "3.14.10"}:
        raise ValueError("Private Python must be an explicitly supported pinned release")
    output.mkdir(parents=True)
    runtime = output / "runtimes/python"
    runtime.mkdir(parents=True)
    for pattern in ("python*.exe", "python*.dll", "vcruntime*.dll", "LICENSE.txt"):
        for source in python_root.glob(pattern):
            regular(source)
            shutil.copyfile(source, runtime / source.name)
    for name in ("Lib", "DLLs"):
        shutil.copytree(
            python_root / name,
            runtime / name,
            ignore=shutil.ignore_patterns(
                "site-packages", "__pycache__", "test", "tests", "idlelib", "turtledemo", "*.pyc"
            ),
        )
    for name in ("bin", "lib", "share"):
        shutil.copytree(postgres_root / name, output / "runtimes/postgres" / name)
    for name in ("server_license.txt", "commandlinetools_3rd_party_licenses.txt"):
        if (postgres_root / name).is_file():
            shutil.copyfile(postgres_root / name, output / "runtimes/postgres" / name)
    shutil.copytree(wheelhouse, output / "wheelhouse")
    subprocess.run(
        [
            str(python_root / "python.exe"),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(wheelhouse),
            "--require-hashes",
            "--only-binary=:all:",
            "--no-compile",
            "--target",
            str(runtime / "Lib/site-packages"),
            "-r",
            str(lock),
        ],
        check=True,
    )
    subprocess.run(
        [
            str(runtime / "python.exe"),
            "-I",
            "-B",
            "-c",
            "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey;import venv;print('private bootstrap verified')",
        ],
        check=True,
    )
    pg_version = (
        subprocess.check_output([str(output / "runtimes/postgres/bin/postgres.exe"), "--version"])
        .decode()
        .strip()
    )
    pins = {
        "schema": "tester-components/v1",
        "python_version": version,
        "postgres_version": pg_version,
        "architecture": "win_amd64",
        "provenance": {
            "python": "Local installed CPython read-only executable/DLL/stdlib allowlist",
            "postgres": "Local installed PostgreSQL read-only bin/lib/share allowlist; data excluded",
            "authentication": "Local content hashes only; no claim of vendor authentication; review and independently pin before production",
        },
        "files": component_files(output),
    }
    write_json(Path(pins_path), pins)
    return pins


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--source-sha", required=True)
    p.add_argument("--components", required=True, type=Path)
    p.add_argument("--component-pins", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    a = p.parse_args()
    result = build(a.repo, a.source_sha, a.components, a.component_pins, a.output)
    print(
        json.dumps(
            {
                "source_sha": a.source_sha,
                "files": len(result["files"]),
                "production_status": "BLOCKED_UNSIGNED",
            }
        )
    )


if __name__ == "__main__":
    main()
