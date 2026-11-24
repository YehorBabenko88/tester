import os
import shutil
import time
from pathlib import Path

from .config import Settings


def is_link(path: Path) -> bool:
    # Windows junctions are reparse points, too (not necessarily symlinks).
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 1024)


def storage_status(settings: Settings) -> dict:
    try:
        root = settings.data_dir
        root.mkdir(parents=True, exist_ok=True)
        if is_link(root):
            raise OSError("data directory must not be a link")
        used = 0
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if not is_link(Path(current) / d)]
            for name in files:
                path = Path(current) / name
                if not is_link(path):
                    used += path.stat().st_size
        free = shutil.disk_usage(root).free
        state = (
            "low_disk"
            if free < settings.min_free_bytes
            else "quota_exceeded"
            if used >= settings.storage_quota_bytes
            else "ok"
        )
        return {"storage": state, "free_bytes": free, "used_bytes": used}
    except OSError:
        return {"storage": "unavailable", "free_bytes": None, "used_bytes": None}


def cleanup_temp(root: Path, older_than_seconds: int = 86400, now: float | None = None) -> int:
    """Remove only direct *.part files in the owned tmp directory; never recurse."""
    folder = root / "tmp"
    if not folder.exists() or is_link(root) or is_link(folder):
        return 0
    cutoff = (time.time() if now is None else now) - older_than_seconds
    removed = 0
    for path in folder.glob("*.part"):
        try:
            if not is_link(path) and path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except FileNotFoundError:
            continue
    return removed
