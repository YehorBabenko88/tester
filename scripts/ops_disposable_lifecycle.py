"""Isolated opt-in lifecycle proof. NOT wired to PC4 or other portable runtimes.

The controller retains real Popen handles; a PID or marker file alone never proves exit.
No terminate(), kill(), reboot, SCM, elevation or PostgreSQL mutations are used.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

import psutil


class LifecycleBlocked(RuntimeError):
    pass


def _digest(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _write_new(path: Path, data: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())


def _load(path: Path) -> dict:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
            raise LifecycleBlocked("Unsafe evidence file")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise LifecycleBlocked("Evidence object required")
        return value
    except (OSError, ValueError) as exc:
        raise LifecycleBlocked("Missing or invalid generation evidence") from exc


@dataclass(frozen=True)
class OwnerProof:
    pid: int
    created: float
    image: str
    cwd: str
    argv: tuple[str, ...]
    username: str
    marker: str
    generation: int


def observe_owned(handle: subprocess.Popen, expected: OwnerProof) -> psutil.Process:
    """Refuse PID reuse, foreign executable, birth, owner, cwd, argv or marker."""
    if handle.pid != expected.pid or handle.poll() is not None:
        raise LifecycleBlocked("Handle not live or PID mismatch")
    try:
        proc = psutil.Process(expected.pid)
        with proc.oneshot():
            observed = OwnerProof(
                pid=proc.pid, created=proc.create_time(), image=proc.exe(),
                cwd=proc.cwd(), argv=tuple(proc.cmdline()), username=proc.username(),
                marker=proc.environ().get("OPS_OWNER_MARKER", ""),
                generation=int(proc.environ().get("OPS_GENERATION", "-1")),
            )
    except (psutil.Error, ValueError, OSError) as exc:
        raise LifecycleBlocked("Unable to attest exact process") from exc
    if observed != expected:
        raise LifecycleBlocked("Foreign, stale or changed process identity")
    return proc


class DisposableLifecycle:
    """Operate ONLY on the exact subprocesses enrolled by this controller."""

    def __init__(self, root: Path, marker: str):
        if not marker or len(marker) < 16:
            raise ValueError("Unpredictable owner marker required")
        self.root = root.resolve(strict=True)
        self.marker = marker
        self.handles: dict[int, subprocess.Popen] = {}
        self.proofs: dict[int, OwnerProof] = {}

    def _verify_sealed_history(self, generations: Path, count: int) -> None:
        """Verify the *entire* hash chain, not just the previous generation.

        Controller handles are required for every ancestor: records alone never
        authorize an unattended restart after the controller is lost.
        """
        previous_sha = None
        for index in range(count):
            directory = generations / f"{index:04d}"
            if directory.is_symlink() or not directory.is_dir():
                raise LifecycleBlocked("Unsafe generation directory")
            permitted = {"manifest.json", "checkpoint.json", "closed.json", "stop.request"}
            if {item.name for item in directory.iterdir()} != permitted:
                raise LifecycleBlocked("Partial or unexpected generation files")
            if any(item.is_symlink() or not item.is_file() for item in directory.iterdir()):
                raise LifecycleBlocked("Symlink or non-file evidence refused")
            manifest = _load(directory / "manifest.json")
            closure = _load(directory / "closed.json")
            checkpoint_path = directory / "checkpoint.json"
            checkpoint = _load(checkpoint_path)
            request = _load(directory / "stop.request")
            handle = self.handles.get(index)
            proof = self.proofs.get(index)
            if handle is None or proof is None or handle.pid != proof.pid or handle.poll() != 0:
                raise LifecycleBlocked("Missing birth-bound clean exit handle")
            if (manifest.get("schema") != "ops-disposable/1"
                    or manifest.get("generation") != index
                    or manifest.get("root") != str(self.root)
                    or manifest.get("owner") != json.loads(json.dumps(vars(proof)))
                    or manifest.get("argv") != list(proof.argv)
                    or not isinstance(manifest.get("config_sha256"), str)
                    or len(manifest["config_sha256"]) != 64
                    or manifest.get("previous_closure_sha256") != previous_sha
                    or checkpoint.get("generation") != index
                    or checkpoint.get("owner_marker") != self.marker
                    or request != {"kind": "cooperative", "generation": index}
                    or closure.get("schema") != "ops-closure/1"
                    or closure.get("generation") != index
                    or closure.get("manifest_sha256") != _digest(manifest)
                    or closure.get("checkpoint_sha256") != hashlib.sha256(checkpoint_path.read_bytes()).hexdigest()
                    or closure.get("exit_code") != 0
                    or closure.get("drained") is not True):
                raise LifecycleBlocked("Generation evidence chain invalid")
            previous_sha = _digest(closure)

    def start(self, argv: list[str], *, environment: dict[str, str] | None = None) -> OwnerProof:
        if not argv or not Path(argv[0]).is_absolute():
            raise LifecycleBlocked("Exact absolute executable required")
        # Bind static caller config, never store the DSN/password itself in receipts.
        fixed_env = {} if environment is None else {
            key: environment[key]
            for key in ("OPS_JOB_ID", "TEST_OPS_ISOLATED_DSN") if key in environment
        }
        config_sha256 = _digest(fixed_env)
        generations = self.root / "ops-generations"
        if generations.is_symlink():
            raise LifecycleBlocked("Symlink generation root refused")
        generations.mkdir(exist_ok=True)
        entries = sorted(generations.iterdir())
        if any(not p.is_dir() or p.is_symlink() or not p.name.isdigit() or len(p.name) != 4
               for p in entries):
            raise LifecycleBlocked("Unknown generation entry")
        numbers = [int(p.name) for p in entries]
        if numbers != list(range(len(numbers))) or len(numbers) >= 32:
            raise LifecycleBlocked("Generation gap or limit reached")
        if numbers:
            self._verify_sealed_history(generations, len(numbers))
            manifest = _load(generations / f"{numbers[-1]:04d}" / "manifest.json")
            if manifest.get("argv") != argv or manifest.get("config_sha256") != config_sha256:
                raise LifecycleBlocked("Exact source or configuration differs")
        generation = len(numbers)
        directory = generations / f"{generation:04d}"
        directory.mkdir(mode=0o700)  # exclusive reservation; never reuse
        stop_path = directory / "stop.request"
        env = dict(os.environ if environment is None else environment)
        env.update(OPS_OWNER_MARKER=self.marker, OPS_GENERATION=str(generation),
                   OPS_STOP_PATH=str(stop_path), OPS_CHECKPOINT_PATH=str(directory / "checkpoint.json"))
        # Reservation remains forever on spawn or attestation failure: fail closed.
        handle = subprocess.Popen(argv, cwd=self.root, env=env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.handles[generation] = handle
        proc = psutil.Process(handle.pid)
        # Initial enrollment uses the actual process observation, never requested metadata.
        for _ in range(40):
            try:
                proof = OwnerProof(handle.pid, proc.create_time(), proc.exe(), proc.cwd(),
                                   tuple(proc.cmdline()), proc.username(),
                                   proc.environ().get("OPS_OWNER_MARKER", ""),
                                   int(proc.environ().get("OPS_GENERATION", "-1")))
                if (proof.marker == self.marker and proof.generation == generation
                    and proof.argv == tuple(argv) and proof.cwd == str(self.root)
                    and Path(proof.image).resolve() == Path(argv[0]).resolve()):
                    break
            except (psutil.Error, ValueError, OSError):
                pass
            time.sleep(0.025)
        else:
            raise LifecycleBlocked("Process could not be enrolled")
        self.proofs[generation] = proof
        manifest = {"schema": "ops-disposable/1", "generation": generation,
                    "root": str(self.root), "owner": vars(proof),
                    "argv": argv, "config_sha256": config_sha256,
                    "previous_closure_sha256": (
                        _digest(_load(generations / f"{generation-1:04d}" / "closed.json"))
                        if generation else None)}
        _write_new(directory / "manifest.json", manifest)
        observe_owned(handle, proof)
        return proof

    def stop_and_seal(self, generation: int, *, timeout: float = 8.0,
                      expected: OwnerProof | None = None) -> dict:
        if generation not in self.handles or generation not in self.proofs:
            raise LifecycleBlocked("Not an enrolled process")
        handle, owner = self.handles[generation], self.proofs[generation]
        if expected is not None and expected != owner:
            raise LifecycleBlocked("Caller ownership evidence mismatch")
        observe_owned(handle, owner)
        directory = self.root / "ops-generations" / f"{generation:04d}"
        manifest = _load(directory / "manifest.json")
        if (manifest.get("schema") != "ops-disposable/1"
                or manifest.get("generation") != generation
                or manifest.get("owner") != json.loads(json.dumps(vars(owner)))
                or manifest.get("root") != str(self.root)
                or manifest.get("argv") != list(owner.argv)):
            raise LifecycleBlocked("Manifest ownership mismatch")
        if (directory / "closed.json").exists() or (directory / "stop.request").exists():
            raise LifecycleBlocked("Prior stop or closure is not reusable")
        _write_new(directory / "stop.request", {"kind": "cooperative", "generation": generation})
        try:
            code = handle.wait(timeout=timeout)  # exact Popen handle, not a polled PID
        except subprocess.TimeoutExpired as exc:
            raise LifecycleBlocked("Drain timeout; process left running, no kill") from exc
        if code != 0:
            raise LifecycleBlocked("Nonzero exit; generation remains unsealed")
        checkpoint_path = directory / "checkpoint.json"
        checkpoint = _load(checkpoint_path)
        if (checkpoint.get("generation") != generation
                or checkpoint.get("owner_marker") != self.marker
                or not isinstance(checkpoint.get("inflight"), str)):
            raise LifecycleBlocked("Drain checkpoint missing or wrong owner")
        receipt = {"schema": "ops-closure/1", "generation": generation,
                   "exit_code": code, "drained": True,
                   "manifest_sha256": _digest(manifest),
                   "checkpoint_sha256": hashlib.sha256(checkpoint_path.read_bytes()).hexdigest()}
        _write_new(directory / "closed.json", receipt)
        return receipt


def resource_receipt(handle: subprocess.Popen, proof: OwnerProof) -> dict:
    """Attest exact live owner before sampling; report unavailable counters as null."""
    process = observe_owned(handle, proof)
    values: dict[str, object] = {"pid": proof.pid, "created": proof.created,
                                 "owner_marker_validated": True, "rss_bytes": None,
                                 "cpu_percent": None, "threads": None,
                                 "read_bytes": None, "write_bytes": None,
                                 "limits_enforced": False,
                                 "pg_integration": "NOT_RUN",
                                 "pc4_integration": "NOT_RUN"}
    for name, operation in {
        "rss_bytes": lambda: process.memory_info().rss,
        "cpu_percent": lambda: process.cpu_percent(interval=0.1),
        "threads": process.num_threads,
        "read_bytes": lambda: process.io_counters().read_bytes,
        "write_bytes": lambda: process.io_counters().write_bytes,
    }.items():
        try:
            values[name] = operation()
        except (psutil.Error, AttributeError, OSError):
            values[name] = None
    values["unknown_fields"] = [key for key, value in values.items() if value is None]
    return values
