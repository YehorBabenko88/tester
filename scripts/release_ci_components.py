"""Provision pinned official Windows components and a strictly fixture-signed CI bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_release import (  # noqa: E402
    build,
    component_files,
    safe_name,
    stage_local_components,
    write_json,
)

PYTHON_URL = "https://api.nuget.org/v3-flatcontainer/python/3.14.10/python.3.14.10.nupkg"
PYTHON_SHA256 = "f54ba67c9951c3d773ea47d2bb095150c037c399f0423a968bcda22809241784"
POSTGRES_URL = "https://sbp.enterprisedb.com/getfile.jsp?fileid=1260725"
POSTGRES_SHA256 = "74f3740a2e7844118857f297c2067d226f8f2b0652de96022ce54bb27cd60a79"


def download(url, expected, target, *, opener=urllib.request.urlopen):
    """Fetch once over verified TLS; execute/extract nothing before exact digest validation."""
    if not url.startswith("https://"):
        raise ValueError("HTTPS component source required")
    target = Path(target)
    h = hashlib.sha256()
    with opener(url, timeout=60) as response, target.open("xb") as stream:
        while chunk := response.read(1024 * 1024):
            stream.write(chunk)
            h.update(chunk)
    if h.hexdigest() != expected:
        raise ValueError("Downloaded component SHA256 mismatch; do not use this file")


def extract(archive, target):
    target = Path(target)
    if target.exists():
        raise ValueError("Fresh extraction directory required")
    with zipfile.ZipFile(archive) as z:
        names, total = set(), 0
        for item in z.infolist():
            name = item.filename.rstrip("/")
            safe_name(name)
            mode = item.external_attr >> 16
            if stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise ValueError("Linked archive member refused")
            if name.casefold() in names:
                raise ValueError("Duplicate archive path refused")
            names.add(name.casefold())
            total += item.file_size
        if len(names) > 100000 or total > 5 * 1024**3:
            raise ValueError("Component archive exceeds extraction budget")
        target.mkdir(parents=True)
        for item in z.infolist():
            p = target / item.filename
            if item.is_dir():
                p.mkdir(parents=True, exist_ok=True)
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                with z.open(item) as src, p.open("xb") as dst:
                    shutil.copyfileobj(src, dst)


def authenticode(path, thumbprint):
    quoted = "'" + str(Path(path).resolve()).replace("'", "''") + "'"
    code = '$ErrorActionPreference="Stop"; $s=Get-AuthenticodeSignature -LiteralPath ' + quoted
    code += (
        "; if($s.Status -ne 'Valid' -or $s.SignerCertificate.Thumbprint -ne '"
        + thumbprint
        + "'){throw 'Pinned supplier Authenticode verification failed'}; $s.SignerCertificate.Subject"
    )
    return subprocess.check_output(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", code],
        text=True,
        env={key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"},
    ).strip()


SIGN_CODE = """import sys,json,hashlib
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from src.api.release_security import sign_release,verify_release
bundle,key,sha,unsigned=map(str,sys.argv[2:])
private=Ed25519PrivateKey.generate()
Path(key).write_bytes(private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
public=hashlib.sha256(private.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).hexdigest()
sign_release(bundle,private_key_path=key,source_sha=sha,password=None,manifest_input=unsigned)
verified=verify_release(bundle,trusted_public_key_sha256=public,expected_source_sha=sha)
print(json.dumps(dict(manifest_sha256=verified.manifest_sha256,public_key_sha256=public)))
"""


def provision(repo, sha, work, evidence, github_env=None):
    if os.name != "nt":
        raise ValueError("Official Windows runtime provisioning requires Windows")
    repo, work, evidence = map(lambda p: Path(p).resolve(), (repo, work, evidence))
    if work.exists():
        raise ValueError("Fresh isolated CI work directory required")
    checked_out = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    if sha != checked_out:
        raise ValueError("Fixture source must equal exact checked out HEAD")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    work.mkdir(parents=True)
    evidence.mkdir(parents=True, exist_ok=True)
    python_archive, postgres_archive = work / "python.nupkg", work / "postgres.zip"
    download(PYTHON_URL, PYTHON_SHA256, python_archive)
    download(POSTGRES_URL, POSTGRES_SHA256, postgres_archive)
    extract(python_archive, work / "python-distribution")
    extract(postgres_archive, work / "postgres-distribution")
    py, pg = work / "python-distribution/tools", work / "postgres-distribution/pgsql"
    suppliers = {
        "python": {
            "url": PYTHON_URL,
            "sha256": PYTHON_SHA256,
            "signer": authenticode(py / "python.exe", "6C435B1CAA2CA423A76C14A6693CD55644C8B4D6"),
        },
        "postgres": {
            "url": POSTGRES_URL,
            "sha256": POSTGRES_SHA256,
            "signer": authenticode(
                pg / "bin/postgres.exe", "7BEDD1269FCCF7A5D95F18274750B79893C06C70"
            ),
        },
        "scope": "Exact reviewed archive hash pins plus executable Authenticode; not a production signing key",
    }
    lock = repo / "requirements-release-windows314.lock"
    wheels = work / "wheelhouse"
    subprocess.run(
        [
            str(py / "python.exe"),
            "-I",
            "-B",
            "-m",
            "pip",
            "download",
            "--only-binary=:all:",
            "--require-hashes",
            "--dest",
            str(wheels),
            "-r",
            str(lock),
        ],
        check=True,
    )
    components, pins = work / "components", work / "component-pins.json"
    values = stage_local_components(py, pg, wheels, lock, components, pins)
    values["provenance"] = suppliers
    write_json(pins, values)
    bundle = work / "bundle"
    manifest = build(repo, sha, components, pins, bundle)
    # Ephemeral fixture key lives OUTSIDE bundle and is never exported as evidence.
    result = subprocess.check_output(
        [
            str(components / "runtimes/python/python.exe"),
            "-I",
            "-B",
            "-c",
            SIGN_CODE,
            str(bundle / "payload"),
            str(bundle),
            str(work / "fixture-key.pem"),
            sha,
            str(work / "bundle.unsigned.json"),
        ],
        text=True,
    )
    signature = json.loads(result)
    if component_files(components) != values["files"]:
        raise ValueError("Provisioning mutated sealed component content")
    exported = {
        "TESTER_RELEASE_BUNDLE": str(bundle),
        "TESTER_RELEASE_MANIFEST_SHA256": signature["manifest_sha256"],
        "TESTER_RELEASE_PUBLIC_KEY_SHA256": signature["public_key_sha256"],
        "TESTER_RELEASE_SOURCE_SHA": sha,
    }
    receipt = {
        "schema": "tester-ci-fixture-bundle/v1",
        "source_sha": sha,
        "files": len(manifest["files"]),
        "production_status": "BLOCKED_FIXTURE_KEY_ONLY",
        "signature_scope": "EPHEMERAL_LOCAL_CI_FIXTURE_NOT_PRODUCTION_TRUST",
        "suppliers": suppliers,
        "fixture": exported,
    }
    write_json(evidence / "component-provisioning-evidence.json", receipt)
    shutil.copyfile(bundle / "release-manifest.json", evidence / "fixture-release-manifest.json")
    if github_env:
        with Path(github_env).open("a", encoding="utf-8") as stream:
            for name, value in exported.items():
                if "\n" in value or "\r" in value:
                    raise ValueError("Invalid environment value")
                stream.write(name + "=" + value + "\n")
    return receipt


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, default=ROOT)
    p.add_argument("--source-sha", required=True)
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--github-env", type=Path)
    a = p.parse_args()
    print(json.dumps(provision(a.repo, a.source_sha, a.work, a.evidence, a.github_env)))


if __name__ == "__main__":
    main()
