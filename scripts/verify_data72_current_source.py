"""Audit frozen DATA source separately from exact current-head admission.

This tool NEVER equates reviewed source 4581e415 to the 9d7e45be branch
HEAD. Git and workspace authenticity must be established externally.
No services, PostgreSQL connection, or active checkout writes.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "3b2eef8a4139195db1a775545e83f1c1f58bf403"
SOURCE = "9d7e45be0872c4c5efa17c06bc107e1dc6a0d2fb"
MASTER = "de95aef6ed46842063fc7c2ef766b7da75fe540e"
FROZEN = "4581e415bd8979a4debee1eb56120bb21b9d95fd"
DELTA = ROOT / "docs/coordination/data72-9d7e-source-delta.json"
RUNTIME = ROOT / "docs/coordination/data72-9d7e-minimal-runtime.json"


def git(*args: str) -> bytes:
    return subprocess.check_output(("git", *args), cwd=ROOT, stderr=subprocess.PIPE)


def blob_hash(raw: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def verify() -> dict[str, object]:
    delta = json.loads(DELTA.read_bytes())
    runtime = json.loads(RUNTIME.read_bytes())
    if (
        delta.get("schema") != "tester-data-head-delta/1"
        or delta.get("source_commit") != SOURCE
        or delta.get("master_checkpoint") != MASTER
        or delta.get("base_commit") != BASE
        or delta.get("status") != "NOT_APPROVED"
        or delta.get("release") != "NO_GO"
        or runtime.get("source_commit") != SOURCE
        or runtime.get("status") != "REVIEW_CANDIDATE_NOT_APPROVED"
        or runtime.get("composition_authorization") is not False
    ):
        raise ValueError("invalid_source_or_admission_scope")
    for sha in (BASE, FROZEN, SOURCE, MASTER):
        if git("rev-parse", sha + "^{commit}").decode().strip() != sha:
            raise ValueError("unknown_commit_" + sha)
    if git("rev-parse", SOURCE + "^{tree}").decode().strip() != runtime["source_tree_sha1"]:
        raise ValueError("current_source_tree_changed")
    approved_raw = git("show", MASTER + ":docs/coordination/master-reviewed-producer-files.json")
    approved_paths = set(json.loads(approved_raw)["producers"]["DATA"])
    rows = git("diff", "--name-status", "--no-renames", BASE, SOURCE).decode().splitlines()
    changed: dict[str, str] = {}
    for row in rows:
        status, path = row.split("\t", 1)
        if status not in {"A", "M"} or path in changed:
            raise ValueError("unsupported_or_duplicate_delta")
        changed[path] = status
    pending = {item["path"]: item for item in delta["pending"]}
    if (
        len(changed) != 31
        or len(approved_paths) != 21
        or len(pending) != 10
        or set(changed) - approved_paths != set(pending)
        or set(approved_paths) - set(changed)
    ):
        raise ValueError("unreviewed_current_head_diff")
    if any(item["path"].startswith("src/") for item in pending.values()):
        raise ValueError("unexpected_pending_runtime_file")
    for path, entry in pending.items():
        tree_row = git("ls-tree", SOURCE, "--", path).decode().strip()
        expected = "100644 blob " + entry["git_blob_sha1"] + "\t" + path
        if entry["mode"] != "100644" or tree_row != expected:
            raise ValueError("untrusted_delta_mode_or_blob:" + path)
        if blob_hash(git("show", SOURCE + ":" + path)) != entry["git_blob_sha1"]:
            raise ValueError("untrusted_delta_bytes:" + path)
    paths = [entry["path"] for entry in runtime["files"]]
    if len(paths) != 5 or len(set(paths)) != 5 or runtime["runtime_files"] != 5:
        raise ValueError("invalid_minimal_runtime_file_set")
    for entry in runtime["files"]:
        path = entry["path"]
        if path not in approved_paths or not path.startswith("src/"):
            raise ValueError("unapproved_runtime_path:" + path)
        expected = "100644 blob " + entry["git_blob_sha1"] + "\t" + path
        if entry["mode"] != "100644" or git("ls-tree", SOURCE, "--", path).decode().strip() != expected:
            raise ValueError("runtime_sha_or_mode_drift:" + path)
        if blob_hash(git("show", SOURCE + ":" + path)) != entry["git_blob_sha1"]:
            raise ValueError("runtime_content_drift:" + path)
    if next(e["git_blob_sha1"] for e in runtime["files"] if e["path"].endswith(
        "008_public_unknown_taker_side.sql"
    )) != "05600c50e3d4598aaa4645ac58e137a719b0eed2":
        raise ValueError("canonical_migration_008_changed")
    return {
        "source_head": SOURCE,
        "historic_frozen_head": FROZEN,
        "changed_paths": 31,
        "already_allowlisted": 21,
        "pending_nonruntime": 10,
        "runtime_payload": 5,
        "minimal_manifest_sha256": hashlib.sha256(RUNTIME.read_bytes()).hexdigest(),
        "delta_manifest_sha256": hashlib.sha256(DELTA.read_bytes()).hexdigest(),
        "status": "PASS_SOURCE_ONLY_NO_EXTERNAL_ATTESTATION",
        "release": "NO_GO",
    }


if __name__ == "__main__":
    try:
        print(json.dumps(verify(), sort_keys=True))
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print("DATA_CURRENT_HEAD_REVIEW_BLOCKED", str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
