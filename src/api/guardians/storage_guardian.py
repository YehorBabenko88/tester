"""Read-only Storage Guardian P0. Never deletes, executes commands, or opens a DB.

The manifest is advisory, not authorization. A separate reviewed executor must
revalidate the root, identities, age and reparse flags before using maintenance.cleanup_temp.
"""
import hashlib
import json
import os
import shutil
import stat
import time
from pathlib import Path

AGE_SECONDS = 86_400
REPARSE = 0x400
MAX_ENTRIES = 10_000


def _safe(path: Path) -> bool:
    """Reject symlinks, junctions and Windows reparse points, including ancestors."""
    try:
        path = Path(os.path.abspath(path))
        for candidate in (path, *path.parents):
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_file_attributes & REPARSE if hasattr(info, "st_file_attributes") else stat.S_ISLNK(info.st_mode):
                return False
        return True
    except (OSError, ValueError):
        return False


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
    root = Path(runtime_root)
    tmp = root / "tmp"
    try:
        if not root.is_dir() or not tmp.is_dir() or not _safe(tmp):
            return {**result, "status": "ALERT_UNVERIFIED_ROOT"}
        usage = shutil.disk_usage(root)
        result["free_bytes"] = usage.free
        result["total_bytes"] = usage.total
        # Only tmp direct files are inventoried. This is NOT a claim about DB size.
        entries = list(tmp.iterdir())
        if len(entries) > MAX_ENTRIES:
            return {**result, "status": "ALERT_SCAN_LIMIT"}
        timestamp = time.time() if now is None else now
        if not isinstance(timestamp, (float, int)) or not 0 < timestamp < 1e15:
            return {**result, "status": "ALERT_INVALID_CLOCK"}
        used = 0
        for path in entries:
            if not _safe(path):
                result["proposals"].append("ALERT_REPARSE_ENTRY")
                continue
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                continue
            used += info.st_size
            if path.suffix != ".part" or info.st_mtime > timestamp - AGE_SECONDS:
                continue
            # Store basename only; never a user-supplied absolute deletion target.
            result["candidates"].append({
                "name": path.name, "bytes": info.st_size,
                "mtime_ns": info.st_mtime_ns, "device": info.st_dev,
                "inode": info.st_ino,
            })
            result["bytes_reclaimable"] += info.st_size
        result["tmp_direct_bytes"] = used
        result["quota_bytes"] = quota_bytes
        result["status"] = "OBSERVED"
        if usage.free < min_free_bytes:
            result["proposals"].append("PROPOSE_PAUSE_NEW_DOWNLOADS")
        if used >= quota_bytes:
            result["proposals"].append("ALERT_TMP_QUOTA")
        if result["candidates"]:
            result["proposals"].append("PROPOSE_DRY_RUN_TEMP_CLEANUP")
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
