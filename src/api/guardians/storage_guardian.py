"""Read-only Storage Guardian P0. Never deletes, executes commands, or opens a DB.

The manifest is advisory, not authorization. A separate reviewed executor must
revalidate the root, identities, age and reparse flags before using maintenance.cleanup_temp.
"""
import hashlib
import json
import math
import os
import shutil
import stat
import time
from pathlib import Path

AGE_SECONDS = 86_400
REPARSE = 0x400
MAX_ENTRIES = 10_000


def _safe(path: Path) -> bool:
    """Reject symlinks and reparse points in every lexical ancestor."""
    try:
        absolute = Path(os.path.abspath(path))
        for candidate in (absolute, *absolute.parents):
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode):
                return False
            if getattr(info, "st_file_attributes", 0) & REPARSE:
                return False
        return True
    except (OSError, ValueError, TypeError):
        return False


def _invalid_root(root: Path) -> bool:
    """Reject traversal before normalization can erase '..'."""
    return not root.is_absolute() or any(part in (".", "..") for part in root.parts)



def _identity(info) -> tuple:
    """Observation identity; cannot authorize deletion or close a TOCTOU race."""
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        getattr(info, "st_file_attributes", 0),
    )


def inventory(runtime_root: Path, *, min_free_bytes: int, quota_bytes: int,
              now: float | None = None, enabled: bool = False) -> dict:
    """Bounded, direct-child-only, no writes. Disabled by default."""
    result = {"schema": "storage-guardian/v1", "enabled": enabled is True,
              "status": "DISABLED", "candidates": [], "bytes_reclaimable": 0,
              "proposals": [], "evidence_sha256": None}
    if enabled is not True:
        return result
    if (type(min_free_bytes) is not int or min_free_bytes < 0 or
            type(quota_bytes) is not int or quota_bytes <= 0):
        return {**result, "status": "ALERT_INVALID_BUDGET"}
    try:
        root = Path(runtime_root)
        if _invalid_root(root):
            return {**result, "status": "ALERT_UNVERIFIED_ROOT"}
        tmp = root / "tmp"
        if not root.is_dir() or not tmp.is_dir() or not _safe(tmp):
            return {**result, "status": "ALERT_UNVERIFIED_ROOT"}
        # Detect some ordinary directory substitutions as well as reparse swaps.
        # These snapshots are evidence only, NEVER a deletion authorization.
        root_identity = _identity(root.lstat())
        tmp_identity = _identity(tmp.lstat())
        usage = shutil.disk_usage(root)
        result["free_bytes"] = usage.free
        result["total_bytes"] = usage.total
        # Only tmp direct files are inventoried. This is NOT a claim about DB size.
        entries = []
        with os.scandir(tmp) as scan:
            for entry in scan:
                entries.append(entry.name)
                if len(entries) > MAX_ENTRIES:
                    return {**result, "status": "ALERT_SCAN_LIMIT"}
        timestamp = time.time() if now is None else now
        if type(timestamp) not in (float, int) or not math.isfinite(timestamp) or not 0 < timestamp < 1e15:
            return {**result, "status": "ALERT_INVALID_CLOCK"}
        used = 0
        for name in entries:
            path = tmp / name
            if not _safe(path):
                return {**result, "status": "ALERT_REPARSE_ENTRY",
                        "candidates": [], "bytes_reclaimable": 0, "proposals": []}
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                continue
            # Second observation catches some swaps, but is not atomic.
            second = path.lstat()
            if _identity(info) != _identity(second) or not _safe(path):
                return {**result, "status": "ALERT_FILE_CHANGED",
                        "candidates": [], "bytes_reclaimable": 0, "proposals": []}
            used += info.st_size
            if not name.endswith(".part") or info.st_mtime > timestamp - AGE_SECONDS:
                continue
            # Store basename only; never a user-supplied absolute deletion target.
            result["candidates"].append({
                "name": name, "bytes": info.st_size,
                "mtime_ns": info.st_mtime_ns, "device": info.st_dev,
                "inode": info.st_ino,
            })
            result["bytes_reclaimable"] += info.st_size
        if (not _safe(tmp) or _identity(root.lstat()) != root_identity
                or _identity(tmp.lstat()) != tmp_identity):
            return {**result, "status": "ALERT_ROOT_CHANGED", "candidates": [],
                    "bytes_reclaimable": 0, "proposals": []}
        result["tmp_direct_bytes"] = used
        result["quota_bytes"] = quota_bytes
        result["status"] = "OBSERVED"
        if usage.free < min_free_bytes:
            result["proposals"].append("PROPOSE_PAUSE_NEW_DOWNLOADS")
        if used >= quota_bytes:
            result["proposals"].append("ALERT_TMP_QUOTA")
        if result["candidates"]:
            result["proposals"].append("PROPOSE_DRY_RUN_TEMP_CLEANUP")
        result["proposals"] = sorted(set(result["proposals"]))
        result["candidates"].sort(key=lambda item: item["name"])
        evidence = {k: v for k, v in result.items() if k != "evidence_sha256"}
        result["evidence_sha256"] = hashlib.sha256(
            json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return result
    except (OSError, ValueError, TypeError):
        return {**result, "status": "ALERT_IO_FAILURE", "candidates": [],
                "bytes_reclaimable": 0, "proposals": []}


def cleanup_authorization(*args, **kwargs):
    """No effector in P0: always fail closed, even for a valid manifest."""
    raise PermissionError("P0 is proposal-only; reviewed maintenance adapter required")
