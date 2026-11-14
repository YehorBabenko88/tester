"""Strict consumer-side generation-scoped cooperative stop validation.

Opt-in only. This validates a request already produced by the original
external Win32 birth-bound observer. It neither signals nor stops processes,
and is NOT connected to installed PC4 API/worker.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.ops_native_windows_witness import NativeBirth, NativeRestartBlocked, sha256


def validate_scoped_stop_request(root: Path, birth: NativeBirth) -> bool:
    """Return False if no request; reject any malformed/foreign request.

    The caller must already have authenticated `birth` against its own
    generation-scoped enrollment. A request-file check by itself does not
    independently prove birth identity or safe process exit.
    """
    if (not isinstance(birth, NativeBirth) or birth.role not in ("api", "worker")
            or type(birth.generation) is not int or not 0 <= birth.generation < 32
            or type(birth.pid) is not int or birth.pid <= 0
            or not isinstance(root, Path) or root.is_symlink() or not root.is_dir()):
        raise NativeRestartBlocked("Invalid scoped stop identity/root")
    owned = root.resolve(strict=True)
    path = owned / f"native-{birth.generation:04d}-{birth.role}.stop.request"
    if path.is_symlink():
        raise NativeRestartBlocked("Symlinked stop request refused")
    if not path.exists():
        return False
    if not path.is_file() or not 0 < path.stat().st_size <= 4096:
        raise NativeRestartBlocked("Oversized or invalid native stop request")
    try:
        raw = path.read_bytes()
        if len(raw) > 4096:
            raise NativeRestartBlocked("Changed native stop request")
        payload = json.loads(raw)
    except (OSError, UnicodeDecodeError, ValueError, TypeError) as exc:
        raise NativeRestartBlocked("Unreadable native stop request") from exc
    expected = {
        "schema": "ops-native-stop/1",
        "role": birth.role,
        "generation": birth.generation,
        "pid": birth.pid,
        "birth_sha256": sha256(vars(birth)),
        "config_sha256": birth.config_sha256,
    }
    if payload != expected:
        raise NativeRestartBlocked("Foreign/stale generation or birth stop request")
    return True
