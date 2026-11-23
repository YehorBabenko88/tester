"""Deterministic offline release staging from immutable Git blobs and pinned components."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
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


SIGNED_SCRIPT_PATHS = {
    "Setup-Tester.ps1",
    "payload/scripts/windows/Start-ReleaseTester.ps1",
    "payload/scripts/windows/Uninstall-ReleaseTester.ps1",
}
SIGNATURE_SUFFIX = re.compile(
    rb"\A[\r\n]*# SIG # Begin signature block\r?\n(?:#[^\r\n]*\r?\n)*# SIG # End signature block[\r\n]*\Z"
)


def verify_overlay_publisher(path, publisher_sha256):
    if not re.fullmatch(r"[0-9a-f]{64}", publisher_sha256 or ""):
        raise ValueError("Independent raw certificate SHA256 pin required")
    if os.name != "nt":
        raise ValueError("Production Authenticode overlay verification requires Windows")
    from src.api.release_install import system_binary

    quoted = "'" + str(Path(path).resolve()).replace("'", "''") + "'"
    code = '$ErrorActionPreference="Stop"; $s=Get-AuthenticodeSignature -LiteralPath ' + quoted
    code += (
        "; if($s.Status -ne 'Valid' -or $null -eq $s.SignerCertificate -or $null -eq $s.TimeStamperCertificate){throw 'Valid timestamped Authenticode signature required'}; $h=[Security.Cryptography.SHA256]::Create(); try{$pin=[BitConverter]::ToString($h.ComputeHash($s.SignerCertificate.RawData)).Replace('-','').ToLowerInvariant()}finally{$h.Dispose()}; if($pin -cne '"
        + publisher_sha256
        + "'){throw 'Independent publisher certificate pin mismatch'}"
    )
    subprocess.run(
        [
            str(system_binary("WindowsPowerShell/v1.0/powershell.exe")),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            code,
        ],
        check=True,
        env={k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"},
    )


@contextlib.contextmanager
def overlay_handles(root, names):
    """Reuse B's no-write/delete native leases and exact admin trust ACL checks."""
    if os.name != "nt":
        raise ValueError("Protected signed overlay verification requires Windows")
    from src.api.release_uninstall import _security_record, _validate_admin_security, _WindowsObject

    directories = {root, *root.parents}
    for name in names:
        directories.update((root / name).parents)
    with contextlib.ExitStack() as stack:
        for path in sorted(directories, key=lambda p: (len(p.parts), str(p).casefold())):
            obj = _WindowsObject(path, delete=False)
            stack.callback(obj.close)
            if not obj.is_dir:
                raise ValueError("Overlay ancestor is not a directory")
            if path == root or root in path.parents:
                _validate_admin_security(_security_record(obj))
        held = {}
        for name in sorted(names):
            obj = _WindowsObject(root / name, delete=False)
            stack.callback(obj.close)
            if obj.is_dir:
                raise ValueError("Overlay script/receipt is not a regular file")
            _validate_admin_security(_security_record(obj))
            held[name] = obj
        yield held


def signed_overlay_inputs(overlay, source_sha, publisher_sha256, original_scripts):
    """Validate frozen owner inputs; no signing, trust import or certificate-store mutation."""
    if not re.fullmatch(r"[0-9a-f]{64}", publisher_sha256 or ""):
        raise ValueError("Independent raw certificate SHA256 pin required")
    root = Path(overlay).absolute()
    for p in (root, *root.parents):
        info = p.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Signed overlay path must be a plain directory")
    actual = {}
    for path in root.rglob("*"):
        info = path.lstat()
        if path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Linked overlay object refused")
        if path.is_file():
            regular(path)
            name = path.relative_to(root).as_posix()
            limit = 65536 if name == "script-signing-receipt.json" else 4 * 1024 * 1024
            if info.st_size > limit:
                raise ValueError("Frozen overlay object exceeds size budget")
            actual[name] = None
    if set(actual) != SIGNED_SCRIPT_PATHS | {"script-signing-receipt.json"}:
        raise ValueError("Frozen overlay requires exactly three canonical scripts and receipt")
    with overlay_handles(root, actual) as held:
        captured = {
            name: obj.read_bytes(
                max_bytes=65536 if name == "script-signing-receipt.json" else 4 * 1024 * 1024
            )
            for name, obj in held.items()
        }
        actual = {name: hashlib.sha256(data).hexdigest() for name, data in captured.items()}
        receipt_bytes = captured["script-signing-receipt.json"]
        if len(receipt_bytes) > 65536:
            raise ValueError("Script receipt exceeds size budget")

        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("Duplicate script receipt key")
                value[key] = item
            return value

        receipt = json.loads(receipt_bytes, object_pairs_hook=unique)
        if (
            set(receipt) != {"schema", "source_sha", "scripts"}
            or receipt["schema"] != "tester-script-signatures/v1"
            or receipt["source_sha"] != source_sha
            or type(receipt["scripts"]) is not list
        ):
            raise ValueError("Frozen script receipt source/schema mismatch")
        frozen = {}
        for row in receipt["scripts"]:
            if type(row) is not dict or set(row) != {
                "path",
                "source_sha256",
                "signed_sha256",
                "certificate_sha256",
            }:
                raise ValueError("Invalid script signing row")
            name = row["path"]
            if type(name) is not str:
                raise ValueError("Invalid signed script path type")
            if name not in SIGNED_SCRIPT_PATHS or name in frozen:
                raise ValueError("Unexpected or duplicate signed script")
            before = original_scripts[name]
            after = captured[name]
            if (
                row["certificate_sha256"] != publisher_sha256
                or row["source_sha256"] != hashlib.sha256(before).hexdigest()
                or row["signed_sha256"] != actual[name]
            ):
                raise ValueError("Frozen source/script/publisher receipt hash mismatch")
            tail = after[len(before) :]
            if any(c > 127 or c < 32 and c not in (9, 10, 13) for c in tail):
                raise ValueError("Signature suffix must be ASCII comment lines")
            if not after.startswith(before) or not SIGNATURE_SUFFIX.fullmatch(tail):
                raise ValueError("Signing changed source body or appended executable bytes")
            verify_overlay_publisher(root / name, publisher_sha256)
            if (
                held[name].digest() != actual[name]
                or hashlib.sha256(after).hexdigest() != actual[name]
            ):
                raise ValueError("Frozen overlay changed during validation")
            frozen[name] = after
        if set(frozen) != SIGNED_SCRIPT_PATHS:
            raise ValueError("Receipt must cover all three signed scripts")
        frozen["script-signing-receipt.json"] = receipt_bytes
        return frozen


def build(
    repo,
    source_sha,
    components,
    pins_path,
    output,
    *,
    signed_overlay=None,
    trusted_publisher_sha256=None,
):
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
    if lock_name.encode() not in sources["src/api/release_install.py"]:
        raise ValueError("Installer dependency lock differs from selected runtime ABI")
    lock = sources[lock_name].decode()
    allowed = set(re.findall(r"--hash=sha256:([0-9a-f]{64})", lock))
    wheels = {name: value for name, value in expected.items() if name.startswith("wheelhouse/")}
    if (
        not allowed
        or set(wheels.values()) != allowed
        or any(not n.endswith(".whl") for n in wheels)
    ):
        raise ValueError("Wheelhouse must match full actual-wheel lock exactly")
    frozen = {}
    if signed_overlay is not None:
        original = {
            (
                "Setup-Tester.ps1"
                if name == "scripts/windows/Setup-Tester.ps1"
                else "payload/" + name
            ): data
            for name, data in sources.items()
        }
        frozen = signed_overlay_inputs(
            signed_overlay, source_sha, trusted_publisher_sha256, original
        )
    elif trusted_publisher_sha256 is not None:
        raise ValueError("Publisher pin without signed overlay is ambiguous")
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
    for name, data in frozen.items():
        (output / name).write_bytes(data)
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
    p.add_argument("--signed-script-overlay", type=Path)
    p.add_argument("--trusted-publisher-sha256")
    a = p.parse_args()
    result = build(
        a.repo,
        a.source_sha,
        a.components,
        a.component_pins,
        a.output,
        signed_overlay=a.signed_script_overlay,
        trusted_publisher_sha256=a.trusted_publisher_sha256,
    )
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
