"""Read-only review of current producer deltas against the frozen MASTER allowlist.

Run only in a disposable full-history checkout. Never modifies source files.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = "3b2eef8a4139195db1a775545e83f1c1f58bf403"
PRODUCERS = {
    "DATA": "327e9be91cca1aec2cd79d6d7c4846d3b024c1b9",
    "RESEARCH": "268a9b0eff8a8fa3a51d1878a724469d3ac778a2",
    "UI": "1e7cf8669eea5181737c30938527ad9e784c9bcf",
    "OPS": "ee58cdb4e5d29c0524f2a0436ceae4a309f80aea",
}


def git(*args: str) -> bytes:
    return subprocess.check_output(("git", *args), cwd=ROOT)


def git_blob_hash(raw: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + bytes([0]) + raw).hexdigest()


def main() -> None:
    manifest_path = "docs/coordination/master-reviewed-producer-files.json"
    committed = git("show", "HEAD:" + manifest_path)
    if (ROOT / manifest_path).is_symlink() or (ROOT / manifest_path).read_bytes() != committed:
        raise SystemExit("mutable or symlinked approved manifest")
    reviewed = json.loads(committed)
    result = {"schema": "master-pending-source-review/1", "base": BASE, "producers": {}}
    for label, sha in PRODUCERS.items():
        assert git("rev-parse", sha + "^{commit}").decode().strip() == sha
        approved = set(reviewed["producers"][label])
        rows = git("diff", "--name-status", "--no-renames", BASE, sha).decode().splitlines()
        observed: set[str] = set()
        pending = []
        for row in rows:
            status, path = row.split("\t", 1)
            if path in observed or status not in {"A", "M"}:
                raise SystemExit(f"invalid source change: {label} {row}")
            observed.add(path)
            if path in approved:
                continue
            mode_line = git("ls-tree", sha, "--", path).decode().strip()
            mode = mode_line.split(" ", 1)[0]
            if mode not in {"100644", "100755"}:
                raise SystemExit(f"unsafe pending Git mode: {label} {path}")
            blob_sha = mode_line.split()[2]
            raw = git("show", f"{sha}:{path}")
            if git_blob_hash(raw) != blob_sha:
                raise SystemExit(f"{label}: Git blob digest mismatch: {path}")
            pending.append(
                {
                    "path": path,
                    "git_blob_sha": blob_sha,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "mode": mode,
                    "role_guess": (
                        "workflow"
                        if path.startswith(".github/workflows/")
                        else "documentation"
                        if path.startswith("docs/")
                        else "test"
                        if path.startswith("tests/")
                        else "script_review_required"
                    ),
                    "approved": False,
                }
            )
        runtime_pending = [
            entry["path"] for entry in pending if entry["path"].startswith(("src/", "frontend/"))
        ]
        if runtime_pending:
            raise SystemExit(
                f"{label}: new runtime paths require explicit review: {runtime_pending}"
            )
        result["producers"][label] = {
            "head": sha,
            "pending": pending,
            "runtime_path_additions": runtime_pending,
            "runtime_path_additions_checked": True,
            "admission_status": "NOT_APPROVED",
        }
    runtime = {}
    for label, sha in PRODUCERS.items():
        rows = []
        for path in sorted(
            p for p in reviewed["producers"][label] if p.startswith(("src/", "frontend/"))
        ):
            mode_line = git("ls-tree", sha, "--", path).decode().strip()
            parts = mode_line.split()
            if len(parts) < 4 or parts[0] not in {"100644", "100755"} or parts[1] != "blob":
                raise SystemExit(f"{label}: invalid reviewed runtime Git entry {path}")
            raw = git("show", f"{sha}:{path}")
            if git_blob_hash(raw) != parts[2]:
                raise SystemExit(f"{label}: approved runtime blob digest mismatch: {path}")
            rows.append(
                {
                    "path": path,
                    "mode": parts[0],
                    "git_blob_sha": parts[2],
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
        runtime[label] = rows
    owners = {}
    for label, rows in runtime.items():
        for entry in rows:
            old = owners.get(entry["path"])
            if old is not None and (old["git_blob_sha"], old["mode"]) != (
                entry["git_blob_sha"],
                entry["mode"],
            ):
                raise SystemExit("runtime source conflict: " + entry["path"])
            owners[entry["path"]] = entry
    result["pending_executable_scripts"] = {
        label: [
            entry
            for entry in result["producers"][label]["pending"]
            if entry["path"].startswith("scripts/")
        ]
        for label in PRODUCERS
    }
    result["approved_runtime_blobs"] = runtime
    result["approved_runtime_unique_paths"] = len(owners)
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
