"""Opt-in native Windows portable shutdown witness (no PC4 runner integration).

This gate requires externally held birth-bound Windows process HANDLES. PID
presence/absence, saved JSON or a TCP listener alone never authorizes restart.
It cannot adopt processes started before this observer enrolled them.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Callable, Protocol


class NativeRestartBlocked(RuntimeError):
    pass


class NativeHandle(Protocol):
    """An external observer's exact process handle with non-forgeable identity."""
    pid: int

    def poll(self) -> int | None: ...
    def wait(self, timeout: float) -> int: ...


@dataclass(frozen=True)
class NativeBirth:
    role: str
    generation: int
    pid: int
    created: float
    image: str
    cwd: str
    argv: tuple[str, ...]
    username: str
    owner_marker: str
    config_sha256: str


def canonical_json(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(value: dict) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def append_new(path: Path, value: dict) -> None:
    """Create-only; do not overwrite a retained record or follow a symlink."""
    if path.parent.is_symlink() or path.is_symlink():
        raise NativeRestartBlocked("Symlinked evidence refused")
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(canonical_json(value).decode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise NativeRestartBlocked("Evidence already exists") from exc


def validate_birth(expected: NativeBirth, actual: NativeBirth,
                   handle: NativeHandle) -> None:
    if expected != actual or expected.role not in ("api", "worker"):
        raise NativeRestartBlocked("Exact birth/image/argv/cwd/owner mismatch")
    if expected.pid <= 0 or expected.generation < 0:
        raise NativeRestartBlocked("Invalid generation or pid")
    if (not expected.owner_marker or len(expected.owner_marker) < 16
            or len(expected.config_sha256) != 64):
        raise NativeRestartBlocked("Missing owner/config identity")
    if handle.pid != expected.pid or handle.poll() is not None:
        raise NativeRestartBlocked("Foreign or no longer live process handle")


def observe_psutil(expected: NativeBirth, handle: NativeHandle,
                   process_lookup: Callable[[int], object]) -> NativeBirth:
    """Re-read OS identity, including process birth and owner marker, before stop."""
    try:
        proc = process_lookup(expected.pid)
        env = proc.environ()
        actual = NativeBirth(
            role=expected.role, generation=expected.generation,
            pid=proc.pid, created=proc.create_time(), image=proc.exe(),
            cwd=proc.cwd(), argv=tuple(proc.cmdline()), username=proc.username(),
            owner_marker=env.get("OPS_OWNER_MARKER", ""),
            config_sha256=env.get("OPS_CONFIG_SHA256", ""),
        )
    except Exception as exc:
        raise NativeRestartBlocked("Process observer unavailable") from exc
    validate_birth(expected, actual, handle)
    return actual


class NativeExitWitness:
    """Observer enrolls both processes BEFORE launch, seals only after both exit."""

    def __init__(self, *, generation: int, api: NativeBirth, worker: NativeBirth,
                 api_handle: NativeHandle, worker_handle: NativeHandle,
                 observe: Callable[[NativeBirth, NativeHandle], NativeBirth],
                 root: Path):
        if api.role != "api" or worker.role != "worker":
            raise NativeRestartBlocked("Two distinct roles required")
        if (api.generation != generation or worker.generation != generation
                or api.pid == worker.pid or api.owner_marker == worker.owner_marker
                or api.config_sha256 != worker.config_sha256):
            raise NativeRestartBlocked("Inconsistent pair identity")
        self.generation = generation
        if root.is_symlink() or not root.is_dir():
            raise NativeRestartBlocked("Native evidence root must be a real directory")
        self.root = root.resolve(strict=True)
        self.items = ((api, api_handle), (worker, worker_handle))
        self.observe = observe
        self._stop_requested = False
        self._sealed = False

    def require_both_live(self) -> None:
        for birth, handle in self.items:
            actual = self.observe(birth, handle)
            validate_birth(birth, actual, handle)

    def request_stop(self, *, stop_for_role: Callable[[NativeBirth], None]) -> None:
        """Caller supplies already-reviewed generation-scoped cooperative signal."""
        if self._stop_requested or self._sealed:
            raise NativeRestartBlocked("Stop already requested or sealed")
        self.require_both_live()  # no side effect until BOTH identities verified
        for birth, _ in self.items:
            stop_for_role(birth)
        self._stop_requested = True

    def seal_exits(self, *, checkpoint_paths: dict[str, Path],
                   timeout: float = 45.0) -> dict:
        if not self._stop_requested or self._sealed or timeout <= 0:
            raise NativeRestartBlocked("No valid stop request")
        if set(checkpoint_paths) != {"api", "worker"}:
            raise NativeRestartBlocked("Both checkpoint witnesses required")
        # Handles must be the exact original ones; no pid-based substitute.
        import subprocess
        exits = {}
        for birth, handle in self.items:
            try:
                code = handle.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                raise NativeRestartBlocked("Drain timeout; no force kill") from exc
            if code != 0 or handle.poll() != 0:
                raise NativeRestartBlocked("Nonzero or unconfirmed clean exit")
            checkpoint = checkpoint_paths[birth.role]
            if checkpoint.is_symlink() or not checkpoint.is_file():
                raise NativeRestartBlocked("Missing generation-scoped checkpoint")
            # No foreign checkpoint outside the owner-controlled native root.
            try:
                actual_path = checkpoint.resolve(strict=True)
                if not actual_path.is_relative_to(self.root) or actual_path.stat().st_size > 1_048_576:
                    raise NativeRestartBlocked("Unsafe or oversized checkpoint")
                content = actual_path.read_bytes()
                payload = json.loads(content)
            except (OSError, ValueError, TypeError) as exc:
                raise NativeRestartBlocked("Unreadable checkpoint") from exc
            if (not isinstance(payload, dict)
                    or payload != {
                        "schema": "ops-native-drain/1",
                        "role": birth.role,
                        "generation": self.generation,
                        "owner_marker": birth.owner_marker,
                        "config_sha256": birth.config_sha256,
                        "drained": True,
                    }):
                raise NativeRestartBlocked("Wrong or incomplete drain receipt")
            exits[birth.role] = {"pid": birth.pid, "created": birth.created,
                                 "birth_sha256": sha256(vars(birth)), "exit_code": code,
                                 "checkpoint_sha256": hashlib.sha256(content).hexdigest(),
                                 "checkpoint_path": actual_path.relative_to(self.root).as_posix()}
        receipt = {
            "schema": "ops-native-win-exit/1", "generation": self.generation,
            "root": str(self.root), "api": exits["api"], "worker": exits["worker"],
            "closed": True,
        }
        append_new(self.root / f"native-generation-{self.generation:04d}-closed.json", receipt)
        self._sealed = True
        return receipt

    def require_restart_allowed(self, receipt: dict) -> None:
        if not self._sealed or not self._stop_requested:
            raise NativeRestartBlocked("No external seal")
        if receipt.get("schema") != "ops-native-win-exit/1" or not receipt.get("closed"):
            raise NativeRestartBlocked("No valid closure record")
        expected = self.root / f"native-generation-{self.generation:04d}-closed.json"
        if expected.is_symlink() or not expected.is_file():
            raise NativeRestartBlocked("Retained closure missing")
        try:
            persisted = json.loads(expected.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise NativeRestartBlocked("Corrupt closure") from exc
        if persisted != receipt or receipt["generation"] != self.generation:
            raise NativeRestartBlocked("External closure differs")
        for birth, handle in self.items:
            proof = receipt.get(birth.role)
            if (not isinstance(proof, dict)
                    or proof.get("birth_sha256") != sha256(vars(birth))
                    or proof.get("exit_code") != 0):
                raise NativeRestartBlocked("Closure does not bind original owner")
            relative = proof.get("checkpoint_path")
            if not isinstance(relative, str) or not relative:
                raise NativeRestartBlocked("Checkpoint location missing")
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts or path == Path("."):
                raise NativeRestartBlocked("Unsafe retained checkpoint location")
            full = self.root / path
            if full.is_symlink() or not full.is_file():
                raise NativeRestartBlocked("Missing retained checkpoint")
            try:
                actual = full.resolve(strict=True)
                if (not actual.is_relative_to(self.root)
                        or hashlib.sha256(actual.read_bytes()).hexdigest()
                           != proof.get("checkpoint_sha256")):
                    raise NativeRestartBlocked("Checkpoint changed after native exit")
            except OSError as exc:
                raise NativeRestartBlocked("Retained checkpoint unreadable") from exc
            if handle.poll() != 0:
                raise NativeRestartBlocked("Old process still alive or changed")
