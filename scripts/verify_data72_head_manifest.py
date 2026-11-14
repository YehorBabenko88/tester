"""Independent DATA HEAD blob and allowlist audit (read-only).

Requires a trusted Git object reader. Neither this file nor a CI environment
variable establishes independent GitHub runner attestation.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEAD = "4581e415bd8979a4debee1eb56120bb21b9d95fd"
BASE = "3b2eef8a4139195db1a775545e83f1c1f58bf403"
MASTER = "298d610771d962ee7b796eac1834d74138768fac"
LEDGER = ROOT / "docs/coordination/data72-current-head-immutable-review.json"


def git(*args: str) -> bytes:
    return subprocess.check_output(("git", *args), cwd=ROOT)


def object_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def verify() -> dict[str, object]:
    manifest = json.loads(LEDGER.read_bytes())
    if (
        manifest["schema"] != "tester-data-immutable-review/2"
        or manifest["status"] != "REVIEW_CANDIDATE_NOT_MASTER_APPROVED"
        or manifest["base_sha"] != BASE
        or manifest["reviewed_data_head"] != HEAD
        or manifest["master_review_head"] != MASTER
        or manifest["release"] != "NO_GO"
    ):
        raise ValueError("wrong immutable DATA review authority")
    if git("rev-parse", HEAD + "^{commit}").decode().strip() != HEAD:
        raise ValueError("missing pinned DATA commit")
    if git("rev-parse", MASTER + "^{commit}").decode().strip() != MASTER:
        raise ValueError("missing pinned MASTER commit")
    if git("rev-parse", HEAD + "^{tree}").decode().strip() != manifest["reviewed_data_tree_sha1"]:
        raise ValueError("wrong DATA source tree")
    raw_allowlist = git("show", MASTER + ":docs/coordination/master-reviewed-producer-files.json")
    if object_sha(raw_allowlist) != manifest["master_allowlist_git_blob_sha1"]:
        raise ValueError("MASTER allowlist Git blob differs")
    old_paths = set(json.loads(raw_allowlist)["producers"]["DATA"])
    rows = git("diff", "--name-status", "--no-renames", BASE, HEAD).decode().splitlines()
    changes: dict[str, str] = {}
    for row in rows:
        status, path = row.split("\t", 1)
        if status not in {"A", "M"} or path in changes:
            raise ValueError("deleted, duplicated, or renamed DATA source")
        changes[path] = status
    entries = manifest["files"]
    paths = [item["path"] for item in entries]
    if len(entries) != 25 or len(set(paths)) != 25 or paths != sorted(paths):
        raise ValueError("not 25 canonical unique paths")
    if set(paths) != set(changes) or len(old_paths) != 21:
        raise ValueError("reviewed DATA changes do not match source diff")
    pending = set(paths) - old_paths
    if pending != {
        ".github/workflows/data-private-generation-review.yml",
        "docs/coordination/data72-private-generation-blob-manifest.json",
        "docs/coordination/data72-private-generation-replay.md",
        "tests/test_data_private_generation_compat.py",
    }:
        raise ValueError("unknown or missing DATA pending-file set")
    for item in entries:
        path = item["path"]
        actual = git("ls-tree", HEAD, "--", path).decode().strip()
        expected = item["mode"] + " blob " + item["git_blob_sha1"] + "\t" + path
        if item["mode"] != "100644" or actual != expected:
            raise ValueError("nonregular or altered Git tree entry: " + path)
        original = git("show", HEAD + ":" + path)
        if object_sha(original) != item["git_blob_sha1"]:
            raise ValueError("Git object content differs: " + path)
        category = (
            "runtime"
            if path.startswith("src/")
            else "ci_driver"
            if path.startswith("scripts/")
            else "test"
            if path.startswith("tests/")
            else "workflow"
            if path.startswith(".github/workflows/")
            else "documentation"
        )
        if category != item["category"] or item["master_298d_allowlisted"] != (path in old_paths):
            raise ValueError("file category/allowlist mismatch: " + path)
    if sum(p.startswith("src/") for p in pending) != 0:
        raise ValueError("runtime expansion requires independent review")
    migration = git("show", HEAD + ":src/storage/migrations/008_public_unknown_taker_side.sql")
    if object_sha(migration) != "05600c50e3d4598aaa4645ac58e137a719b0eed2":
        raise ValueError("migration 008 changed")
    return {
        "data_head": HEAD,
        "master_head": MASTER,
        "count": len(entries),
        "approved": len(old_paths),
        "pending": len(pending),
        "migration_008": object_sha(migration),
        "manifest_sha256": hashlib.sha256(LEDGER.read_bytes()).hexdigest(),
        "status": "PASS_SOURCE_IDENTITY_ONLY_NOT_EXTERNAL_ATTESTATION",
    }


if __name__ == "__main__":
    try:
        print(json.dumps(verify(), sort_keys=True))
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
        print("DATA_REVIEW_FAIL_CLOSED", str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
