"""Executable *disposable-only* Windows launch integration; never install on PC4.

Reservation is durable/create-only. Process handles are NOT persisted; after a
controller crash restart is deliberately blocked rather than adopting old PIDs.
All child commands are exact caller-supplied trusted fixture argv.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
from typing import Callable, Mapping

from scripts.ops_native_admission import NativeAdmission, NativePin
from scripts.ops_native_windows_witness import (
    NativeBirth, NativeExitWitness, NativeRestartBlocked, observe_psutil,
    request_native_scoped_stop,
)
from scripts.ops_native_windows_rollback import EnrolledChild, rollback_partial_start
from scripts.ops_win32_handle import WindowsOriginalHandle


@dataclass(frozen=True)
class LaunchedPair:
    generation: int
    api: EnrolledChild
    worker: EnrolledChild
    witness: NativeExitWitness


class DisposableWin32Launcher:
    """Win32 owned-subprocess orchestration; NO API service / PC4 integration."""

    def __init__(self, root: Path, pin: NativePin, *,
                 pg_probe: Callable[[], Mapping],
                 leases_probe: Callable[[], Mapping[str, str]],
                 process_lookup: Callable[[int], object] | None = None):
        if os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true":
            raise NativeRestartBlocked("Native fixture launcher restricted to hosted Windows CI")
        self.root = root.resolve(strict=True)
        self.admission = NativeAdmission(self.root, pin)
        self.pg_probe = pg_probe
        self.leases_probe = leases_probe
        if process_lookup is None:
            import psutil
            process_lookup = psutil.Process
        self.process_lookup = process_lookup
        self._pairs: dict[int, LaunchedPair] = {}
        self._children: list[subprocess.Popen] = []

    def _observe(self, birth, handle):
        return observe_psutil(birth, handle, self.process_lookup)

    def _previous_exits(self, generation: int) -> bool:
        pair = self._pairs.get(generation)
        if pair is None:
            return False  # crash/restarted controller must never adopt old PID
        try:
            pair.witness.require_restart_allowed(
                json.loads((self.root / f"native-generation-{generation:04d}-closed.json").read_text())
            )
            return True
        except (NativeRestartBlocked, OSError, ValueError, KeyError):
            return False

    def _spawn(self, role: str, generation: int, argv: tuple[str, ...],
               config_sha: str) -> EnrolledChild:
        if (not argv or not Path(argv[0]).is_absolute() or role not in ("api", "worker")
                or len(config_sha) != 64):
            raise NativeRestartBlocked("Exact fixture executable and config required")
        marker = "ops-fixture-" + role + "-" + secrets.token_hex(24)
        checkpoint = self.root / f"native-{generation:04d}-{role}.checkpoint.json"
        stop = self.root / f"native-{generation:04d}-{role}.stop.request"
        if checkpoint.exists() or stop.exists():
            raise NativeRestartBlocked("Prior generation records exist")
        env = dict(os.environ)
        env.update(OPS_OWNER_MARKER=marker, OPS_CONFIG_SHA256=config_sha,
                   OPS_ROLE=role, OPS_GENERATION=str(generation),
                   OPS_STOP_PATH=str(stop), OPS_CHECKPOINT_PATH=str(checkpoint))
        child = subprocess.Popen(argv, cwd=self.root, env=env,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._children.append(child)
        handle = WindowsOriginalHandle(child)
        until = time.monotonic() + 6.0
        while time.monotonic() < until:
            try:
                proc = self.process_lookup(child.pid)
                if (proc.environ().get("OPS_OWNER_MARKER") == marker
                        and tuple(proc.cmdline()) == argv
                        and handle.poll() is None):
                    birth = NativeBirth(
                        role=role, generation=generation, pid=proc.pid,
                        created=proc.create_time(), image=proc.exe(),
                        cwd=proc.cwd(), argv=tuple(proc.cmdline()),
                        username=proc.username(), owner_marker=marker,
                        config_sha256=config_sha)
                    self._observe(birth, handle)
                    return EnrolledChild(birth, handle, checkpoint)
            except Exception:
                pass
            time.sleep(.025)
        raise NativeRestartBlocked("Native child could not be birth-enrolled")

    def _post_spawn_fence(self) -> None:
        """Recheck PG incarnation and leases AFTER both child births are proven.

        Pre-reservation checks alone leave a race in which a lease starts or
        PostgreSQL restarts during subprocess startup. Refuse and cooperatively
        roll back; never use a stale preflight observation as permission.
        This is a disposable guard, NOT an atomic installed-runtime PG lock.
        """
        pin = self.admission.pin
        pg = dict(self.pg_probe())
        if (pg.get("pg_system_identifier") != pin.pg_system_identifier
                or pg.get("pg_postmaster_start") != pin.pg_postmaster_start):
            raise NativeRestartBlocked("Post-spawn PostgreSQL incarnation changed")
        if "active_running" in pg and (
            type(pg["active_running"]) is not int or pg["active_running"] != 0
        ):
            raise NativeRestartBlocked("Post-spawn running PostgreSQL lease")
        leases = dict(self.leases_probe())
        if any(value not in {"queued", "completed", "failed", "paused"}
               for value in leases.values()):
            raise NativeRestartBlocked("Post-spawn active/unverified lease")

    def start_pair(self, *, api_argv: tuple[str, ...],
                   worker_argv: tuple[str, ...]) -> LaunchedPair:
        pin = self.admission.pin
        # Every reservation (including incomplete ones) is retained forever.
        generation = self.admission.next_generation(
            previous_exit_validator=self._previous_exits,
            pg_probe=self.pg_probe, leases_probe=self.leases_probe)
        self.admission.reserve(
            generation, previous_exit_validator=self._previous_exits,
            pg_probe=self.pg_probe, leases_probe=self.leases_probe)
        enrolled: list[EnrolledChild] = []
        try:
            enrolled.append(self._spawn("worker", generation, worker_argv, pin.config_sha256))
            enrolled.append(self._spawn("api", generation, api_argv, pin.config_sha256))
            api = next(item for item in enrolled if item.birth.role == "api")
            worker = next(item for item in enrolled if item.birth.role == "worker")
            witness = NativeExitWitness(
                generation=generation, api=api.birth, worker=worker.birth,
                api_handle=api.handle, worker_handle=worker.handle,
                observe=self._observe, root=self.root)
            witness.require_both_live()
            self._post_spawn_fence()  # a lease/PG could change while children spawn
            pair = LaunchedPair(generation, api, worker, witness)
            self._pairs[generation] = pair
            return pair
        except Exception:
            if enrolled:
                rollback_partial_start(
                    root=self.root, generation=generation,
                    children=tuple(enrolled), observe=self._observe)
            raise NativeRestartBlocked("Native startup failed; reserved generation remains blocked") from None

    def stop_and_seal(self, pair: LaunchedPair) -> dict:
        if self._pairs.get(pair.generation) is not pair:
            raise NativeRestartBlocked("Foreign native pair")
        witness = pair.witness
        witness.request_stop(
            stop_for_role=lambda birth: request_native_scoped_stop(self.root, birth))
        receipt = witness.seal_exits(
            checkpoint_paths={"api": pair.api.checkpoint_path,
                              "worker": pair.worker.checkpoint_path},
            timeout=8)
        # Only the actual original-handle witness can authorize closure.
        # PG incarnation + lease probes are rechecked inside the admission gate.
        self.admission.seal_reservation(
            pair.generation, witness=witness, receipt=receipt,
            pg_probe=self.pg_probe, leases_probe=self.leases_probe)
        return receipt
