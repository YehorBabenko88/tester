"""Fail-closed native Windows *partial-start* rollback, opt-in only.

Never advances a failed generation. Original handles and birth-bound ownership
must be attested before even a cooperative stop request is created.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Callable

from scripts.ops_native_windows_witness import (
    NativeBirth,
    NativeHandle,
    NativeRestartBlocked,
    append_new,
    validate_birth,
    request_native_scoped_stop,
    sha256,
)


@dataclass(frozen=True)
class EnrolledChild:
    birth: NativeBirth
    handle: NativeHandle
    checkpoint_path: Path


def deny_partial_generation_reuse(root: Path, generation: int) -> None:
    path = root / f"native-generation-{generation:04d}-rollback.json"
    if path.exists() or path.is_symlink():
        raise NativeRestartBlocked("Failed native generation cannot be reused")


def rollback_partial_start(
    *,
    root: Path,
    generation: int,
    children: tuple[EnrolledChild, ...],
    observe: Callable[[NativeBirth, NativeHandle], NativeBirth],
    timeout: float = 30.0,
) -> dict:
    """Seal failed-start receipt, never grant restart (even on clean rollback)."""
    if not 0 < timeout <= 60 or root.is_symlink() or not root.is_dir():
        raise NativeRestartBlocked("Invalid native rollback scope")
    root = root.resolve(strict=True)
    deny_partial_generation_reuse(root, generation)
    if (generation < 0 or not children or len(children) > 2
            or len({item.birth.role for item in children}) != len(children)):
        raise NativeRestartBlocked("Invalid partial API/worker enrolment")
    if any(item.birth.generation != generation or item.birth.role not in {"api", "worker"}
           for item in children):
        raise NativeRestartBlocked("Foreign native generation or role")
    if (root / f"native-generation-{generation:04d}-closed.json").exists():
        raise NativeRestartBlocked("Already-closed generation cannot roll back")

    receipt: dict = {
        "schema": "ops-native-partial-rollback/1",
        "generation": generation,
        "status": "BLOCKED",
        "restart_permitted": False,
        "sealed_roles": {},
        "unverified_roles": [],
    }
    try:
        # No side effects until ALL enrolled live identities have been attested.
        for item in children:
            validate_birth(item.birth, observe(item.birth, item.handle), item.handle)

        for item in children:
            request_native_scoped_stop(root, item.birth)

        for item in children:
            try:
                exit_code = item.handle.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                raise NativeRestartBlocked("Cooperative rollback timeout") from None
            if exit_code != 0 or item.handle.poll() != 0:
                raise NativeRestartBlocked("Unverified rollback process exit")
            path = item.checkpoint_path
            if path.is_symlink() or not path.is_file():
                raise NativeRestartBlocked("Rollback drain receipt missing")
            absolute = path.resolve(strict=True)
            if not absolute.is_relative_to(root) or absolute.stat().st_size > 1_048_576:
                raise NativeRestartBlocked("Foreign rollback drain receipt")
            content = absolute.read_bytes()
            try:
                value = json.loads(content)
            except (TypeError, ValueError) as exc:
                raise NativeRestartBlocked("Corrupt rollback drain") from exc
            expected = {
                "schema": "ops-native-drain/1",
                "role": item.birth.role,
                "generation": generation,
                "owner_marker": item.birth.owner_marker,
                "config_sha256": item.birth.config_sha256,
                "drained": True,
            }
            if value != expected:
                raise NativeRestartBlocked("Wrong rollback drain identity")
            receipt["sealed_roles"][item.birth.role] = {
                "birth_sha256": sha256(vars(item.birth)),
                "checkpoint_sha256": hashlib.sha256(content).hexdigest(),
                "exit_code": exit_code,
            }
        receipt["status"] = "SEALED_PARTIAL"
    except (NativeRestartBlocked, OSError):
        receipt["unverified_roles"] = [
            item.birth.role for item in children if item.birth.role not in receipt["sealed_roles"]
        ]
        # No force termination, no PID adoption, no retry of partial generation.
    append_new(root / f"native-generation-{generation:04d}-rollback.json", receipt)
    return receipt
