"""Verify immutable source ancestry and all Guardian-owned assembly bytes."""

import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
RELEASE_BASE = "e64e30d8369626197403b3962f91bb350656e4c9"
PINS = {
    "master": "bd619eb88e9444c3b414739a27f3053fb240251e",
    "code": "96731dfe493e5967c1336d2315fd7187381bee70",
    "data": "e7cc55a24a12ffcc9da82dda1f532fd13ae78af9",
    "storage": "a58d0d428ac003dc8d9d3b829a97aeaa115e895f",
    "exchange": "eea38f54340a943886a9ad95803626ddf5f49331",
}


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root)


def load_release_overrides(root):
    """Read explicit release deltas; immutable input pins are never replaced."""
    path = Path(root) / "docs/release/source-overrides.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or data.get("schema") != "tester-release-source-overrides/v1"
        or data.get("baseline_sha") != RELEASE_BASE
        or not isinstance(data.get("files"), dict)
    ):
        raise ValueError("Unsupported release override schema or baseline")
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", RELEASE_BASE, "HEAD"], cwd=root, check=True
    )
    return data["files"]


def _validate_overrides(root, overrides, owners):
    allowed = {name: digest for files in owners.values() for name, digest in files.items()}
    if not isinstance(overrides, dict) or not set(overrides) <= set(allowed):
        raise ValueError("Override outside original owner paths")
    for name, entry in overrides.items():
        if not isinstance(entry, dict) or entry.get("baseline_sha256") != allowed[name]:
            raise ValueError("Override baseline digest differs from immutable source")
        digest = entry.get("candidate_sha256")
        if type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid candidate digest")
        if type(entry.get("reason")) is not str or not entry["reason"].strip():
            raise ValueError("Override must explain the changed verified logic")
        tests = entry.get("regression_tests")
        if not isinstance(tests, list) or not tests:
            raise ValueError("Override needs regression tests")
        for test in tests:
            if (
                type(test) is not str
                or not test.startswith("tests/")
                or "\\" in test
                or ":" in test
                or ".." in PurePosixPath(test).parts
                or PurePosixPath(test).as_posix() != test
                or not test.endswith(".py")
                or not (root / test).is_file()
                or (root / test).is_symlink()
            ):
                raise ValueError("Override regression path missing or unsafe")


def verify(root, manifest, *, overrides=None):
    root = Path(root)
    if manifest.get("sources") != PINS or manifest.get("guardians_enabled") is not False:
        raise ValueError("Unexpected sources or Guardian enablement")
    if manifest.get("schema_version") != "tester-unified-sources/v1":
        raise ValueError("Unsupported sources schema")
    owners = manifest.get("owner_files", {})
    if set(owners) != set(PINS) - {"master"}:
        raise ValueError("Incomplete owner paths")
    if overrides is None:
        overrides = load_release_overrides(root)
    _validate_overrides(root, overrides, owners)
    count = 0
    for role, sha in PINS.items():
        # An ancestor check does not modify a branch or invoke user code.
        subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=root, check=True)
        if role == "master":
            continue
        expected = {
            f"src/api/guardians/{role}_guardian.py",
            f"tests/platform/test_{role}_guardian.py",
            f"docs/guardians/{role}-guardian.md",
            f"docs/guardians/workstream-{role}-p0.md",
        }
        changed = set(git(root, "diff", "--name-only", PINS["master"], sha).decode().splitlines())
        if set(owners[role]) != expected or changed != expected:
            raise ValueError("Unexpected owner paths")
        for name, digest in owners[role].items():
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("Invalid digest")
            original = hashlib.sha256(git(root, "show", f"{sha}:{name}")).hexdigest()
            # Git stores LF; normalize the Windows checkout's CRLF representation.
            observed = hashlib.sha256(
                (root / name).read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest()
            target = overrides.get(name, {}).get("candidate_sha256", digest)
            if original != digest or observed != target:
                raise ValueError(f"Source digest drift: {name}")
            count += 1
    return count


if __name__ == "__main__":
    manifest = json.loads((ROOT / "docs/guardians/unified-candidate-sources.json").read_text())
    print(f"PASS: {verify(ROOT, manifest)} immutable owner sources and declared release bytes")
