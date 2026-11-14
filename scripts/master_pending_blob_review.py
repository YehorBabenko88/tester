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
    "RESEARCH": "af143bb73277e7bffea8ae76d84fd0ed7a707ab0",
    "UI": "1d6e34907ae2e4972b8ddf2d4b7301cf0920eb3f",
    "OPS": "9c0ee3a44e78d16d8beb34fbaf2973ee2e126386",
}


def git(*args: str) -> bytes:
    return subprocess.check_output(("git", *args), cwd=ROOT)


def main() -> None:
    reviewed = json.loads((ROOT / "docs/coordination/master-reviewed-producer-files.json").read_text())
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
            pending.append({
                "path": path,
                "git_blob_sha": blob_sha,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "mode": mode,
                "role_guess": (
                    "workflow" if path.startswith(".github/workflows/")
                    else "documentation" if path.startswith("docs/")
                    else "test" if path.startswith("tests/")
                    else "script_review_required"
                ),
                "approved": False,
            })
        runtime_pending = [entry["path"] for entry in pending if entry["path"].startswith(("src/", "frontend/"))]
        if runtime_pending:
            raise SystemExit(f"{label}: new runtime paths require explicit review: {runtime_pending}")
        result["producers"][label] = {
            "head": sha, "pending": pending,
            "runtime_path_additions": runtime_pending,
            "runtime_path_additions_checked": True,
            "admission_status": "NOT_APPROVED",
        }
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
