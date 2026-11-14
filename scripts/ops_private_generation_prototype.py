"""CI-only private generation composer prototype. NO network, git fetch or launcher.

Trusted caller must supply pinned Git blob bytes and a private owner-only
workspace on a trusted filesystem; environment flags are not attestation.
Never point this at an active checkout or production installation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

SCHEMA = "ops-private-generation/1"
HEX64 = re.compile(r"[a-f0-9]{64}\Z")
HEX40 = re.compile(r"[a-f0-9]{40}\Z")


class CompositionRefused(ValueError):
    pass


@dataclass(frozen=True)
class ReviewedBlob:
    path: str
    data: bytes
    sha256: str
    git_blob_sha1: str
    mode: int = 0o644


def hash256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def git_hash(raw: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\x00" + raw).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def valid_path(path: str) -> tuple[str, ...]:
    if (not isinstance(path, str) or not 0 < len(path) < 512
            or path.startswith("/") or "\\" in path or "%" in path
            or ":" in path or "\x00" in path or "//" in path
            or path.endswith("/") or any(p in ("", ".", "..") for p in path.split("/"))):
        raise CompositionRefused("invalid_or_traversing_path")
    parts = tuple(path.split("/"))
    if parts[0] not in ("src", "tests", "scripts", "docs", "frontend", ".github"):
        raise CompositionRefused("unreviewed_path_prefix")
    return parts


def private_root(root: Path) -> None:
    if not isinstance(root, Path) or not root.is_absolute() or root.is_symlink():
        raise CompositionRefused("untrusted_root")
    s = root.lstat()
    if (not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid()
            or stat.S_IMODE(s.st_mode) != 0o700):
        raise CompositionRefused("workspace_not_owner_only_0700")


def verify_sources(blobs: Iterable[ReviewedBlob], manifest: bytes, pin: str,
                   source_pins: dict[str, str]) -> list[ReviewedBlob]:
    if not isinstance(manifest, bytes) or not HEX64.fullmatch(pin) or hash256(manifest) != pin:
        raise CompositionRefused("untrusted_manifest")
    try:
        doc = json.loads(manifest)
    except (UnicodeError, ValueError) as exc:
        raise CompositionRefused("bad_manifest_json") from exc
    if (doc.get("schema") != SCHEMA or not isinstance(doc.get("files"), list)
            or doc.get("source_pins") != source_pins):
        raise CompositionRefused("bad_manifest_schema")
    expected = {}
    for item in doc["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "git_blob_sha1", "mode"}:
            raise CompositionRefused("bad_reviewed_entry")
        path = item["path"]
        valid_path(path)
        if (path in expected or not HEX64.fullmatch(item["sha256"])
                or not HEX40.fullmatch(item["git_blob_sha1"]) or item["mode"] not in (0o644, 0o755)):
            raise CompositionRefused("duplicate_or_invalid_manifest_entry")
        expected[path] = item
    if not expected:
        raise CompositionRefused("empty_reviewed_set")
    observed = {}
    for blob in blobs:
        if not isinstance(blob, ReviewedBlob):
            raise CompositionRefused("not_a_reviewed_blob")
        valid_path(blob.path)
        if blob.path in observed or blob.path not in expected:
            raise CompositionRefused("duplicate_or_unknown_source")
        if (not isinstance(blob.data, bytes) or blob.mode not in (0o644, 0o755)
                or hash256(blob.data) != blob.sha256 or git_hash(blob.data) != blob.git_blob_sha1):
            raise CompositionRefused("unverified_git_blob")
        if expected[blob.path] != dict(path=blob.path, sha256=blob.sha256,
                                      git_blob_sha1=blob.git_blob_sha1, mode=blob.mode):
            raise CompositionRefused("reviewed_blob_mismatch")
        observed[blob.path] = blob
    if set(observed) != set(expected):
        raise CompositionRefused("missing_reviewed_source")
    return [observed[path] for path in sorted(observed)]


def sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_exclusive(path: Path, raw: bytes, mode=0o600) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as file:
        os.fchmod(file.fileno(), mode)
        file.write(raw)
        file.flush()
        os.fsync(file.fileno())


def verify_tree(candidate: Path, blobs: list[ReviewedBlob], *, sealed=False) -> str:
    expected = {b.path: b for b in blobs}
    expected_dirs = set()
    for rel in expected:
        parts = rel.split("/")
        expected_dirs.update("/".join(parts[:i]) for i in range(1, len(parts)))
    discovered = {}
    for folder, dirs, files in os.walk(candidate, followlinks=False):
        for dirname in dirs:
            d = Path(folder) / dirname
            rel_dir = d.relative_to(candidate).as_posix()
            if (rel_dir not in expected_dirs or d.is_symlink()
                    or not stat.S_ISDIR(d.lstat().st_mode)):
                raise CompositionRefused("unsafe_staging_directory")
        for filename in files:
            path = Path(folder) / filename
            rel = path.relative_to(candidate).as_posix()
            st = path.lstat()
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise CompositionRefused("unsafe_staging_inode")
            if sealed and rel == "generation.json":
                continue
            if rel not in expected:
                raise CompositionRefused("unexpected_staging_file")
            blob = expected[rel]
            if stat.S_IMODE(st.st_mode) != blob.mode:
                raise CompositionRefused("staged_mode_mismatch")
            content = path.read_bytes()
            if hash256(content) != blob.sha256 or git_hash(content) != blob.git_blob_sha1:
                raise CompositionRefused("corrupted_staging_blob")
            discovered[rel] = {"sha256": blob.sha256, "mode": blob.mode}
    if set(discovered) != set(expected):
        raise CompositionRefused("incomplete_staging_tree")
    return hash256(canonical(discovered))


def publish_generation(root: Path, *, blobs: Iterable[ReviewedBlob],
                       manifest: bytes, manifest_sha256: str,
                       source_pins: dict[str, str],
                       fault: Callable[[str, Path], None] | None = None) -> str:
    """Create a complete unreferenced generation and switch CURRENT last.

    The trusted host must exclude other writers and directory relocations.
    This is a prototype, NOT runner attestation or production deployment.
    """
    private_root(root)
    if (not isinstance(source_pins, dict) or not source_pins or any(
            not isinstance(k, str) or not HEX40.fullmatch(v)
            for k, v in source_pins.items())):
        raise CompositionRefused("invalid_source_pins")
    approved = verify_sources(blobs, manifest, manifest_sha256, source_pins)
    if any(p.name not in {"CURRENT", "generations", ".staging"} for p in root.iterdir()):
        raise CompositionRefused("unexpected_workspace_file")
    gens, staging = root / "generations", root / ".staging"
    for p in (gens, staging):
        if p.is_symlink():
            raise CompositionRefused("symlink_workspace")
        if not p.exists():
            p.mkdir(mode=0o700)
        if not p.is_dir() or stat.S_IMODE(p.stat().st_mode) != 0o700:
            raise CompositionRefused("nonprivate_staging_parent")
    generation = uuid.uuid4().hex
    candidate = staging / generation
    candidate.mkdir(mode=0o700)
    final = gens / generation
    pointer_tmp = root / (".CURRENT." + generation)
    try:
        for blob in approved:
            path = candidate / blob.path
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            write_exclusive(path, blob.data, blob.mode)
            if fault:
                fault("after_file", path)
        digest = verify_tree(candidate, approved)
        if fault:
            fault("after_verify", candidate)
        if verify_tree(candidate, approved) != digest:
            raise CompositionRefused("stage_changed_after_verify")
        receipt = canonical(dict(schema=SCHEMA, generation=generation,
                                 source_pins=source_pins, manifest_sha256=manifest_sha256,
                                 tree_sha256=digest, status="VERIFIED"))
        write_exclusive(candidate / "generation.json", receipt)
        for folder, _, _ in os.walk(candidate, topdown=False):
            sync_dir(Path(folder))
        sync_dir(staging)
        if fault:
            fault("before_publish", candidate)
        if verify_tree(candidate, approved, sealed=True) != digest:
            raise CompositionRefused("stage_changed_before_publish")
        if final.exists():
            raise CompositionRefused("generation_collision")
        os.rename(candidate, final)  # unique destination, same filesystem
        sync_dir(gens)
        # Detect relocation-transition mutations before publishing CURRENT.
        # Same-UID adversarial writes AFTER this verification remain BLOCKED.
        if verify_tree(final, approved, sealed=True) != digest:
            raise CompositionRefused("published_generation_mutated")
        if fault:
            fault("after_publish_before_current", final)
        write_exclusive(pointer_tmp, canonical(dict(
            generation=generation, tree_sha256=digest,
            manifest_sha256=manifest_sha256)))
        os.replace(pointer_tmp, root / "CURRENT")
        sync_dir(root)
        return generation
    except BaseException:
        # Fail closed. A CURRENT swap may already be visible even if root
        # fsync then raises. Do not unlink a newly published or pinned tree.
        # Likewise preserve staging and temporary pointer for forensic
        # recovery: same-UID writers make recursive cleanup unsafe.
        # Incomplete candidates remain unreferenced and are NEVER activated.
        raise


def read_current(root: Path, *, manifest: bytes, manifest_sha256: str) -> str:
    """Require a sealed, complete, unmodified generation; refuse partial trees."""
    private_root(root)
    if not isinstance(manifest, bytes) or hash256(manifest) != manifest_sha256:
        raise CompositionRefused("untrusted_manifest")
    pointer = root / "CURRENT"
    if not pointer.exists() or pointer.is_symlink() or not stat.S_ISREG(pointer.lstat().st_mode):
        raise CompositionRefused("current_not_published")
    try:
        current = json.loads(pointer.read_bytes())
        gen = current["generation"]
        if not isinstance(gen, str) or not re.fullmatch(r"[a-f0-9]{32}", gen):
            raise ValueError("invalid generation")
        candidate = root / "generations" / gen
        if candidate.is_symlink() or not candidate.is_dir():
            raise ValueError("invalid candidate path")
        receipt_path = candidate / "generation.json"
        if receipt_path.is_symlink():
            raise ValueError("symlink receipt")
        receipt = json.loads(receipt_path.read_bytes())
        if (receipt["status"] != "VERIFIED" or receipt["generation"] != gen
                or receipt["manifest_sha256"] != manifest_sha256
                or current["manifest_sha256"] != manifest_sha256):
            raise ValueError("invalid receipt")
        doc = json.loads(manifest)
        if receipt["source_pins"] != doc.get("source_pins"):
            raise ValueError("source pins mismatch")
        expected = [ReviewedBlob(e["path"], b"", e["sha256"],
                                 e["git_blob_sha1"], e["mode"]) for e in doc["files"]]
        digest = verify_tree(candidate, expected, sealed=True)
        if digest != receipt["tree_sha256"] or digest != current["tree_sha256"]:
            raise ValueError("inconsistent tree digest")
        return gen
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CompositionRefused("incomplete_or_corrupted_generation") from exc



def read_pinned(root: Path, generation: str, *, manifest: bytes,
                manifest_sha256: str) -> str:
    """Revalidate a named generation without following a later CURRENT switch."""
    private_root(root)
    if not isinstance(generation, str) or not re.fullmatch(r"[a-f0-9]{32}", generation):
        raise CompositionRefused("invalid_pinned_generation")
    if not isinstance(manifest, bytes) or hash256(manifest) != manifest_sha256:
        raise CompositionRefused("untrusted_manifest")
    try:
        doc = json.loads(manifest)
        if doc.get("schema") != SCHEMA or not isinstance(doc.get("files"), list):
            raise ValueError("bad manifest")
        candidate = root / "generations" / generation
        if candidate.is_symlink() or not candidate.is_dir():
            raise ValueError("missing generation")
        receipt_path = candidate / "generation.json"
        if receipt_path.is_symlink() or not stat.S_ISREG(receipt_path.lstat().st_mode):
            raise ValueError("bad receipt inode")
        receipt = json.loads(receipt_path.read_bytes())
        if (receipt.get("status") != "VERIFIED"
                or receipt.get("generation") != generation
                or receipt.get("manifest_sha256") != manifest_sha256
                or receipt.get("source_pins") != doc.get("source_pins")):
            raise ValueError("bad receipt")
        expected = [ReviewedBlob(e["path"], b"", e["sha256"],
                                 e["git_blob_sha1"], e["mode"]) for e in doc["files"]]
        digest = verify_tree(candidate, expected, sealed=True)
        if digest != receipt.get("tree_sha256"):
            raise ValueError("bad tree digest")
        return generation
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CompositionRefused("invalid_or_corrupted_pinned_generation") from exc


def retire_generation(*_args, **_kwargs) -> None:
    """Fail closed: safe GC needs cross-process reader leases and recovery."""
    raise CompositionRefused("gc_disabled_without_reader_leases")


def inspect_recovery(root: Path, *, manifest: bytes,
                     manifest_sha256: str) -> dict:
    """Read-only, fail-closed recovery inspection; never rewires CURRENT.

    No candidate is selected by creation time and no directory is deleted.
    A valid historical generation is *not* automatically safe to activate:
    source trust, concurrent writers, and crash-durability are external gates.
    """
    private_root(root)
    if not isinstance(manifest, bytes) or not HEX64.fullmatch(manifest_sha256):
        raise CompositionRefused("untrusted_manifest")
    if hash256(manifest) != manifest_sha256:
        raise CompositionRefused("untrusted_manifest")
    try:
        doc = json.loads(manifest)
        if (doc.get("schema") != SCHEMA or not isinstance(doc.get("files"), list)
                or not isinstance(doc.get("source_pins"), dict)):
            raise ValueError("invalid recovery manifest")
        # Parse the reviewed entries even when CURRENT is missing.
        entries = doc["files"]
        seen = set()
        for item in entries:
            if not isinstance(item, dict) or set(item) != {
                    "path", "sha256", "git_blob_sha1", "mode"}:
                raise ValueError("invalid manifest entry")
            valid_path(item["path"])
            if (item["path"] in seen or item["mode"] not in (0o644, 0o755)
                    or not HEX64.fullmatch(item["sha256"])
                    or not HEX40.fullmatch(item["git_blob_sha1"])):
                raise ValueError("invalid or duplicate manifest entry")
            seen.add(item["path"])
        if not seen:
            raise ValueError("empty manifest")
    except (ValueError, KeyError, TypeError) as exc:
        raise CompositionRefused("invalid_recovery_manifest") from exc

    entries_in_root = sorted(root.iterdir(), key=lambda item: item.name)
    extra = [p.name for p in entries_in_root
             if p.name not in {"CURRENT", "generations", ".staging"}
             and not p.name.startswith(".CURRENT.")]
    temp_pointers = [p.name for p in entries_in_root
                     if p.name.startswith(".CURRENT.")]
    problems = []
    if extra:
        problems.append("unexpected_root_entries")
    if temp_pointers:
        problems.append("unfinished_pointer_write")
    for name in ("generations", ".staging"):
        folder = root / name
        if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
            problems.append("unsafe_" + name.replace(".", "") + "_directory")
    if problems and any(item.startswith("unsafe_") for item in problems):
        return {
            "state": "BLOCKED_UNSAFE_WORKSPACE", "current": None,
            "verified": [], "unverified": [], "staging": [],
            "pointer_temps": temp_pointers, "problems": problems,
            "auto_switch": False, "gc_allowed": False,
        }
    gens = root / "generations"
    staging = root / ".staging"
    verified, unverified = [], []
    if gens.is_dir():
        for item in sorted(gens.iterdir(), key=lambda p: p.name):
            if (not re.fullmatch(r"[a-f0-9]{32}", item.name)
                    or item.is_symlink() or not item.is_dir()):
                unverified.append(item.name)
                continue
            try:
                read_pinned(root, item.name, manifest=manifest,
                            manifest_sha256=manifest_sha256)
            except CompositionRefused:
                unverified.append(item.name)
            else:
                verified.append(item.name)
    stages = sorted(p.name for p in staging.iterdir()) if staging.is_dir() else []
    current = None
    try:
        current = read_current(root, manifest=manifest,
                               manifest_sha256=manifest_sha256)
        state = "CURRENT_VALID"
    except CompositionRefused:
        pointer = root / "CURRENT"
        state = ("CURRENT_INVALID" if pointer.exists() or pointer.is_symlink()
                 else "CURRENT_MISSING")
        problems.append(state.lower())
    if unverified:
        problems.append("unverified_published_generations")
    if stages:
        problems.append("unreferenced_staging_entries")
    return {
        "state": state, "current": current,
        "verified": verified, "unverified": unverified,
        "staging": stages, "pointer_temps": temp_pointers,
        "problems": problems, "auto_switch": False, "gc_allowed": False,
    }


def recovery_candidate(root: Path, generation: str, *, manifest: bytes,
                       manifest_sha256: str) -> str:
    """Read-only operator-selected candidate check, never alter CURRENT.

    This confirms only the current bytes, not their crash persistence or
    authenticity against a hostile same-UID process.
    """
    return read_pinned(root, generation, manifest=manifest,
                       manifest_sha256=manifest_sha256)
