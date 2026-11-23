"""Verify immutable source ancestry and all Guardian-owned assembly bytes."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINS = {
    "master": "bd619eb88e9444c3b414739a27f3053fb240251e",
    "code": "96731dfe493e5967c1336d2315fd7187381bee70",
    "data": "e7cc55a24a12ffcc9da82dda1f532fd13ae78af9",
    "storage": "a58d0d428ac003dc8d9d3b829a97aeaa115e895f",
    "exchange": "eea38f54340a943886a9ad95803626ddf5f49331",
}


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root)


def verify(root, manifest):
    root = Path(root)
    if manifest.get("sources") != PINS or manifest.get("guardians_enabled") is not False:
        raise ValueError("Unexpected sources or Guardian enablement")
    if manifest.get("schema_version") != "tester-unified-sources/v1":
        raise ValueError("Unsupported sources schema")
    owners = manifest.get("owner_files", {})
    if set(owners) != set(PINS) - {"master"}:
        raise ValueError("Incomplete owner paths")
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
            if original != digest or observed != digest:
                raise ValueError(f"Source digest drift: {name}")
            count += 1
    return count


if __name__ == "__main__":
    manifest = json.loads((ROOT / "docs/guardians/unified-candidate-sources.json").read_text())
    print(f"PASS: {verify(ROOT, manifest)} owned files and five immutable ancestors")
