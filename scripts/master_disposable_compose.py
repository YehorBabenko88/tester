"""Build a disposable, source-pinned candidate in a GitHub Actions workspace.

This never modifies a branch, machine installation, or PostgreSQL instance.
All producer changes are replayed against the immutable PC4 source tree.
"""
import hashlib
import os
import pathlib
import subprocess
import sys

BASE = "3b2eef8a4139195db1a775545e83f1c1f58bf403"
PRODUCERS = (
    ("DATA", "d5bfdad445a1eefb9dc12bf2725a00f171e2c321"),
    ("RESEARCH", "7ec35810434e3c9159f36712a4186595ac637efa"),
    ("UI", "a61a2c3bceec8747d7b2424d91c9a4a6c3ec9d96"),
    ("OPS", "0d4649f3a7cd3595ea58757f2abaf2f1b0558667"),
)
ROOT = pathlib.Path(__file__).resolve().parents[1]
ALLOWED = (".github/", "docs/", "src/", "frontend/", "scripts/", "tests/")
# No source may alter the existing PC4 launcher or deployment bootstrap.
PROTECTED = ("src/integration/pc4_runtime.py", "src/api/worker.py", "pyproject.toml")


def git(*args):
    return subprocess.check_output(("git", *args), cwd=ROOT)


def main():
    if os.environ.get("CI") != "true":
        raise SystemExit("CI-only disposable source composition")
    if git("rev-parse", "HEAD").decode().strip() != os.environ.get("EXPECTED_STAGING_SHA"):
        raise SystemExit("staging SHA changed; refuse unpinned composition")
    seen = {}
    changes = []
    for label, sha in PRODUCERS:
        if git("rev-parse", sha + "^{commit}").decode().strip() != sha:
            raise SystemExit(f"{label}: missing exact producer SHA")
        # The UI branch includes the #70 ancestry. A common BASE diff captures it.
        rows = git("diff", "--name-status", "--no-renames", BASE, sha).decode().splitlines()
        for row in rows:
            status, path = row.split("\t", 1)
            if status not in ("A", "M") or not path.startswith(ALLOWED) or path in PROTECTED:
                raise SystemExit(f"refusing {label} status/path: {row}")
            raw = git("show", f"{sha}:{path}")
            digest = hashlib.sha256(raw).hexdigest()
            previous = seen.get(path)
            if previous is not None:
                if previous[1] != digest:
                    raise SystemExit(f"source conflict: {path}: {previous[0]} vs {label}")
                continue
            seen[path] = (label, digest)
            changes.append((label, path, raw))
    for label, path, raw in changes:
        target = ROOT / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    print(f"COMPOSED_UNCOMMITTED_FILES={len(changes)}")
    for label, path, raw in changes:
        print(f"{label}\t{hashlib.sha256(raw).hexdigest()}\t{path}")
    print("COMPOSITION_ONLY: not a commit, not a release, no native launcher activated")


if __name__ == "__main__":
    main()
