"""Build a disposable, source-pinned candidate in a GitHub Actions workspace.

This never modifies a branch, machine installation, or PostgreSQL instance.
All producer changes are replayed against the immutable PC4 source tree.
"""
import hashlib
import json
import os
import pathlib
import subprocess

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
    if (os.environ.get("CI") != "true" or os.environ.get("GITHUB_ACTIONS") != "true"
            or not os.environ.get("GITHUB_WORKSPACE")
            or pathlib.Path(os.environ["GITHUB_WORKSPACE"]).resolve() != ROOT):
        raise SystemExit("refuse non-Actions workspace (environment flags alone are not attestation)")
    if (os.environ.get("GITHUB_REPOSITORY") != "YehorBabenko88/tester"
            or os.environ.get("GITHUB_SHA") != os.environ.get("EXPECTED_STAGING_SHA")
            or not os.environ.get("GITHUB_RUN_ID", "").isdigit()
            or os.environ.get("RUNNER_OS") != "Linux"):
        raise SystemExit("refuse unbound Actions job metadata")
    if ROOT.is_symlink() or not ROOT.is_dir():
        raise SystemExit("unsafe checkout root")
    if git("rev-parse", "HEAD").decode().strip() != os.environ.get("EXPECTED_STAGING_SHA"):
        raise SystemExit("staging SHA changed; refuse unpinned composition")
    reviewed = json.loads((ROOT / "docs/coordination/master-reviewed-producer-files.json").read_text(encoding="utf-8"))
    if reviewed.get("schema") != "master-reviewed-producer-files/1":
        raise SystemExit("invalid reviewed file manifest")
    approved = {key: set(paths) for key, paths in reviewed["producers"].items()}
    seen = {}
    changes = []
    for label, sha in PRODUCERS:
        if git("rev-parse", sha + "^{commit}").decode().strip() != sha:
            raise SystemExit(f"{label}: missing exact producer SHA")
        # The UI branch includes the #70 ancestry. A common BASE diff captures it.
        rows = git("diff", "--name-status", "--no-renames", BASE, sha).decode().splitlines()
        for row in rows:
            status, path = row.split("\t", 1)
            if status not in ("A", "M") or not path.startswith(ALLOWED) or path in PROTECTED or path not in approved[label]:
                raise SystemExit(f"refusing {label} status/path: {row}")
            # Only regular Git blobs are eligible: reject symlinks and gitlinks.
            mode_row = git("ls-tree", sha, "--", path).decode().strip()
            if not mode_row.startswith("100644 blob ") and not mode_row.startswith("100755 blob "):
                raise SystemExit(f"nonregular producer source: {path}")
            raw = git("show", f"{sha}:{path}")
            digest = hashlib.sha256(raw).hexdigest()
            previous = seen.get(path)
            if previous is not None:
                if previous[1] != digest:
                    raise SystemExit(f"source conflict: {path}: {previous[0]} vs {label}")
                continue
            seen[path] = (label, digest)
            changes.append((label, path, raw))
    # Preflight every destination before any writes, including parent symlinks.
    for label, path, raw in changes:
        rel = pathlib.PurePosixPath(path)
        if rel.is_absolute() or ".." in rel.parts or "\\x00" in path or "\\" in path:
            raise SystemExit(f"unsafe relative path: {path}")
        target = ROOT.joinpath(*rel.parts)
        node = ROOT
        for component in rel.parts:
            node = node / component
            if node.is_symlink():
                raise SystemExit(f"symlink destination or parent: {path}")
            if node.exists() and not (node.is_dir() or node.is_file()):
                raise SystemExit(f"nonregular destination: {path}")
        if target.exists() and not target.is_file():
            raise SystemExit(f"non-file destination: {path}")
    # Descriptor-relative writes prevent symlink swaps between preflight and open.
    # This workflow runs only on Linux, with O_NOFOLLOW and directory fds.
    rootfd = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for label, path, raw in changes:
            parts = pathlib.PurePosixPath(path).parts
            parentfd = os.dup(rootfd)
            try:
                for part in parts[:-1]:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=parentfd)
                    except FileExistsError:
                        pass
                    nextfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parentfd)
                    os.close(parentfd)
                    parentfd = nextfd
                fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600, dir_fd=parentfd)
                with os.fdopen(fd, "wb") as output:
                    output.write(raw)
            finally:
                os.close(parentfd)
    finally:
        os.close(rootfd)
    print(f"COMPOSED_UNCOMMITTED_FILES={len(changes)}")
    for label, path, raw in changes:
        print(f"{label}\t{hashlib.sha256(raw).hexdigest()}\t{path}")
    print("COMPOSITION_ONLY: not a commit, not a release, no native launcher activated")


if __name__ == "__main__":
    main()
