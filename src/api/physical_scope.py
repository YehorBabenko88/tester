"""Fixed physical acceptance scope. No SCM, ACL, deletion or process actions."""

import hashlib
import json
import os
import re
import stat
import subprocess
from datetime import datetime
from pathlib import Path
from uuid import UUID

ROOT = Path("C:/ProgramData/TesterPhysicalTest20261107")
SERVICE = "TesterPhysicalTest20261107"
PG_SERVICE = "TesterPhysicalPg20261107"
OWNER = SERVICE + "/v1"


class ScopeViolation(ValueError):
    pass


def plain_path(path):
    path = Path(os.path.abspath(path))
    for parent in [path, *path.parents]:
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        # Following stat can hide a junction's reparse attributes behind its target.
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ScopeViolation("Reparse points are outside owned scope")
        if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            raise ScopeViolation("Hard-linked files are outside owned scope")
    return path


def validate_owner(root=ROOT):
    root = plain_path(root)
    path = plain_path(root / "installation-owner.json")
    if not path.is_file() or path.stat().st_size > 16384:
        raise ScopeViolation("Owner marker missing/oversized")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if set(data) != {
            "schema_version",
            "repository",
            "installation_id",
            "host_fingerprint",
            "root",
            "services",
            "created_at",
        }:
            raise ValueError()
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ValueError()
        if (
            data["repository"] != "YehorBabenko88/tester"
            or data["host_fingerprint"] != "658ac47c38eb17a6"
        ):
            raise ValueError()
        if str(UUID(data["installation_id"])) != data["installation_id"]:
            raise ValueError()
        if plain_path(data["root"]) != root or data["services"] != [SERVICE, PG_SERVICE]:
            raise ValueError()
        created = datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
        if created.utcoffset() is None or created.utcoffset().total_seconds() != 0:
            raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ScopeViolation("Owner marker is outside physical acceptance scope") from exc
    return data


def release_path(release, root=ROOT):
    root, release = plain_path(root), plain_path(release)
    if release.parent != root / "releases" or not re.fullmatch("[0-9a-f]{40}", release.name):
        raise ScopeViolation("Release must be directly inside the fixed releases directory")
    return release


def image_path(release, root=ROOT):
    release = release_path(release, root)
    return subprocess.list2cmdline(
        [
            str(plain_path(root) / "venv/Scripts/python.exe"),
            "-I",
            "-B",
            str(release / "scripts/windows/physical_service.py"),
            "--dispatch",
        ]
    )


def validate_release(release, digest, *, root=ROOT):
    release = release_path(release, root)
    if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
        raise ScopeViolation("Approved manifest SHA256 required")
    manifest = plain_path(release / "physical-manifest.json")
    if not manifest.is_file():
        raise ScopeViolation("Manifest missing")
    raw = manifest.read_bytes()
    if len(raw) > 4 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != digest:
        raise ScopeViolation("Manifest differs from approved digest")
    data = json.loads(raw)
    if (
        set(data) != {"schema_version", "release_sha", "files"}
        or data["schema_version"] != "1.0"
        or data["release_sha"] != release.name
    ):
        raise ScopeViolation("Invalid release manifest")
    files = data["files"]
    if not isinstance(files, dict) or not files or len(files) > 10000:
        raise ScopeViolation("Invalid inventory")
    found = set()
    for item in release.rglob("*"):
        plain_path(item)
        if item.is_file() and item != manifest:
            found.add(item.relative_to(release).as_posix())
    if found != set(files) or len({p.casefold() for p in files}) != len(files):
        raise ScopeViolation("Release inventory differs")
    total = 0
    for rel, expected in files.items():
        if (
            not isinstance(rel, str)
            or "\\" in rel
            or ":" in rel
            or any(p in {"", "..", "."} for p in rel.split("/"))
        ):
            raise ScopeViolation("Unsafe inventory path")
        path = plain_path(release / rel)
        if not path.is_relative_to(release) or path.stat().st_size > 64 * 1024 * 1024:
            raise ScopeViolation("Inventory entry escapes release or exceeds bound")
        total += path.stat().st_size
        if total > 256 * 1024**2:
            raise ScopeViolation("Release exceeds total payload bound")
        if (
            not isinstance(expected, str)
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
        ):
            raise ScopeViolation("Release content differs")
    return release


def owned_service(name, image, options, *, root=ROOT):
    owner = validate_owner(root)
    if (
        options.get("InstallationId") != owner["installation_id"]
        or name != SERVICE
        or options.get("Owner") != OWNER
        or plain_path(options.get("Root", "")) != plain_path(root)
    ):
        raise ScopeViolation("Existing service ownership is not proven")
    release = release_path(options.get("Release", ""), root)
    if image != image_path(release, root):
        raise ScopeViolation("Existing service command is not owned")
    if not re.fullmatch("[0-9a-f]{64}", options.get("ManifestSHA256", "")):
        raise ScopeViolation("Existing service manifest marker missing")
    return True


def failure_budget(runtime, record=None):
    runtime = plain_path(runtime)
    path = plain_path(runtime / "scm-failures.json")
    state = (
        json.loads(path.read_text()) if path.exists() else {"failures": 0, "last_signature": None}
    )
    count = state.get("failures")
    if type(count) is not int or count < 0 or count >= 3:
        raise ScopeViolation(
            "Physical service failure budget exhausted or invalid; MASTER intervention required"
        )
    if record is not None:
        state = {"failures": count + 1, "last_signature": record}
        temp = plain_path(runtime / "scm-failures.pending")
        temp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(temp, path)
        return count + 1
    return count
