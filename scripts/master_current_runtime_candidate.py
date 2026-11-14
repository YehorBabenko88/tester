"""CI-only exact-current runtime candidate, never writes into the active checkout.

Creates a private 0700 temporary tree with reviewed src/frontend blobs only.
This is NOT the OPS generation publisher or a deployable release.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import stat
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = "3b2eef8a4139195db1a775545e83f1c1f58bf403"
PRODUCERS = {
    "DATA": "327e9be91cca1aec2cd79d6d7c4846d3b024c1b9",
    "RESEARCH": "268a9b0eff8a8fa3a51d1878a724469d3ac778a2",
    "UI": "016e0d4190c290b96637662e6fa856a252abf2a8",
    "OPS": "ee58cdb4e5d29c0524f2a0436ceae4a309f80aea",
}


def git(*args: str) -> bytes:
    return subprocess.check_output(("git", *args), cwd=ROOT)


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + bytes([0]) + data).hexdigest()


def main() -> None:
    if os.environ.get("CI") != "true" or os.environ.get("RUNNER_OS") != "Linux":
        raise SystemExit("CI-only isolated source verification")
    manifest_path = "docs/coordination/master-reviewed-producer-files.json"
    original = git("show", "HEAD:" + manifest_path)
    if (ROOT / manifest_path).is_symlink() or (ROOT / manifest_path).read_bytes() != original:
        raise SystemExit("untrusted worktree manifest")
    manifest = json.loads(original)
    sources = {}
    for label, sha in PRODUCERS.items():
        if git("rev-parse", sha + "^{commit}").decode().strip() != sha:
            raise SystemExit("unpinned producer " + label)
        changed = {row.split("\t", 1)[1] for row in git("diff", "--name-status", "--no-renames", BASE, sha).decode().splitlines()}
        approved = set(manifest["producers"][label])
        for path in sorted(approved):
            if not path.startswith(("src/", "frontend/")):
                continue
            if path not in changed or path.startswith("/") or ".." in pathlib.PurePosixPath(path).parts:
                raise SystemExit("invalid approved runtime path " + path)
            entry = git("ls-tree", sha, "--", path).decode().strip().split()
            if len(entry) != 4 or entry[0] not in ("100644", "100755") or entry[1] != "blob":
                raise SystemExit("unsafe runtime mode " + path)
            raw = git("show", f"{sha}:{path}")
            if blob_sha(raw) != entry[2]:
                raise SystemExit("blob integrity mismatch " + path)
            previous = sources.get(path)
            if previous is not None and (previous[0], previous[1]) != (entry[2], entry[0]):
                raise SystemExit("conflicting producer runtime path " + path)
            sources[path] = (entry[2], entry[0], raw, label)
    with tempfile.TemporaryDirectory(prefix="master-runtime-candidate-") as temp:
        stage = pathlib.Path(temp)
        os.chmod(stage, 0o700)
        if stat.S_IMODE(stage.stat().st_mode) != 0o700:
            raise SystemExit("stage not private")
        for path, (sha, mode, raw, label) in sorted(sources.items()):
            target = stage.joinpath(*path.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with target.open("xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            target.chmod(int(mode[-3:], 8))
            if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(raw).digest():
                raise SystemExit("staged source changed " + path)
        receipt = {
            "schema": "master-current-runtime-source-smoke/1",
            "status": "CI_ONLY_NOT_PUBLISHED",
            "source_pins": PRODUCERS,
            "runtime_files": {path: {"blob": sha, "mode": mode, "producer": label}
                              for path, (sha, mode, raw, label) in sorted(sources.items())},
        }
        print(json.dumps(receipt, sort_keys=True, indent=2))
    print("NO_ACTIVE_CHECKOUT_WRITE_NO_NATIVE_LAUNCHER_NO_RELEASE")


if __name__ == "__main__":
    main()
