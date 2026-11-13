"""Two-process disposable API/worker restart witness, never a PC4 adapter.

Every subprocess is born under this controller and observed through an exact
Popen handle. A restarted controller cannot reconstruct a missing handle from
PID/receipt text. No forced termination, native SCM, elevation or reboot.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from scripts.ops_disposable_lifecycle import (
    DisposableLifecycle,
    LifecycleBlocked,
    OwnerProof,
    _digest,
    _load,
    _write_new,
    observe_owned,
    resource_receipt,
)


@dataclass(frozen=True)
class PairProof:
    generation: int
    api: OwnerProof
    worker: OwnerProof


class DisposableApiWorker:
    """Fail-closed paired lifecycle using generation-scoped stop files."""

    def __init__(self, root: Path, marker: str):
        self.root = root.resolve(strict=True)
        self.marker = marker
        for name in ("api", "worker"):
            path = self.root / name
            if path.is_symlink():
                raise LifecycleBlocked("Symlink process root")
            path.mkdir(mode=0o700, exist_ok=True)
        self.api = DisposableLifecycle(self.root / "api", marker + ":api")
        self.worker = DisposableLifecycle(self.root / "worker", marker + ":worker")
        self.pairs: dict[int, PairProof] = {}
        self.pair_root = self.root / "pair-generations"
        if self.pair_root.is_symlink():
            raise LifecycleBlocked("Symlink pair root")
        self.pair_root.mkdir(mode=0o700, exist_ok=True)

    def _verify_history(self) -> int:
        entries = sorted(self.pair_root.iterdir())
        if any(not p.is_dir() or p.is_symlink() or not p.name.isdigit() or len(p.name) != 4
               for p in entries):
            raise LifecycleBlocked("Unexpected pair generation")
        generations = [int(p.name) for p in entries]
        if generations != list(range(len(generations))) or len(generations) >= 32:
            raise LifecycleBlocked("Pair generation gap or limit")
        previous_sha = None
        for generation in generations:
            path = self.pair_root / f"{generation:04d}"
            if {p.name for p in path.iterdir()} != {"manifest.json", "activated.request", "closed.json"}:
                raise LifecycleBlocked("Partial pair evidence")
            if any(p.is_symlink() or not p.is_file() for p in path.iterdir()):
                raise LifecycleBlocked("Unsafe pair evidence")
            manifest, closure = _load(path / "manifest.json"), _load(path / "closed.json")
            activated = _load(path / "activated.request")
            proof = self.pairs.get(generation)
            if proof is None or proof.generation != generation:
                raise LifecycleBlocked("Unverifiable original pair handles")
            if (manifest.get("schema") != "ops-disposable-pair/1"
                    or manifest.get("generation") != generation
                    or manifest.get("root") != str(self.root)
                    or manifest.get("api") != json.loads(json.dumps(vars(proof.api)))
                    or manifest.get("worker") != json.loads(json.dumps(vars(proof.worker)))
                    or manifest.get("previous_closure_sha256") != previous_sha
                    or activated != {"schema": "ops-disposable-pair-ready/1",
                                     "generation": generation,
                                     "manifest_sha256": _digest(manifest)}):
                raise LifecycleBlocked("Pair manifest changed")
            expected_closures = {}
            for name, ctl in (("api", self.api), ("worker", self.worker)):
                prior = ctl.root / "ops-generations" / f"{generation:04d}" / "closed.json"
                expected_closures[name] = _digest(_load(prior))
                handle = ctl.handles.get(generation)
                if handle is None or handle.poll() != 0:
                    raise LifecycleBlocked("API/worker exact handle not closed cleanly")
            if (closure.get("schema") != "ops-disposable-pair-closure/1"
                    or closure.get("generation") != generation
                    or closure.get("manifest_sha256") != _digest(manifest)
                    or closure.get("api_closure_sha256") != expected_closures["api"]
                    or closure.get("worker_closure_sha256") != expected_closures["worker"]
                    or closure.get("api_exit_code") != 0
                    or closure.get("worker_exit_code") != 0):
                raise LifecycleBlocked("Invalid paired external exit witness")
            previous_sha = _digest(closure)
        return len(generations)

    def start_pair(self, api_argv: list[str], worker_argv: list[str],
                   *, environment: dict[str, str] | None = None) -> PairProof:
        generation = self._verify_history()  # no new processes before all old proofs
        # Refuse a prior partial child reservation and unclosed prior processes.
        for ctl in (self.api, self.worker):
            generations = ctl.root / "ops-generations"
            entries = list(generations.iterdir()) if generations.exists() else []
            if len(entries) != generation:
                raise LifecycleBlocked("Unmatched API/worker generation")
        # Verify both previous child chains and their fixed executable/config
        # *before* spawning either new process; no partial start on bad source.
        if generation:
            immutable = {} if environment is None else {
                key: environment[key]
                for key in ("OPS_JOB_ID", "TEST_OPS_ISOLATED_DSN") if key in environment
            }
            config_sha = _digest(immutable)
            for ctl, argv in ((self.api, api_argv), (self.worker, worker_argv)):
                history = ctl.root / "ops-generations"
                ctl._verify_sealed_history(history, generation)
                previous = _load(history / f"{generation - 1:04d}" / "manifest.json")
                if previous.get("argv") != argv or previous.get("config_sha256") != config_sha:
                    raise LifecycleBlocked("API/worker previous source/configuration changed")
        path = self.pair_root / f"{generation:04d}"
        path.mkdir(mode=0o700)  # append-only reservation
        # Worker may not claim durable queue jobs before API, manifest and
        # ownership proofs have all been committed by this controller.
        child_env = dict(environment or {})
        if environment is None:
            import os
            child_env = dict(os.environ)
        child_env["OPS_PAIR_READY_PATH"] = str(path / "activated.request")
        # Failure cannot release the reservation. Roll back *only* owned,
        # birth-attested children by their cooperative generation stop path.
        try:
            worker = self.worker.start(worker_argv, environment=child_env)
            api = self.api.start(api_argv, environment=child_env)
            proof = PairProof(generation=generation, api=api, worker=worker)
            previous = (_digest(_load(self.pair_root / f"{generation - 1:04d}" / "closed.json"))
                        if generation else None)
            manifest = {
                "schema": "ops-disposable-pair/1", "generation": generation,
                "root": str(self.root), "api": vars(api), "worker": vars(worker),
                "previous_closure_sha256": previous,
            }
            _write_new(path / "manifest.json", manifest)
            self.pairs[generation] = proof
            # An API that exits between enrollment and activation must not
            # permit the worker to claim any durable job.
            self.assert_both_owned(proof)
            # Last fallible operation before return: the worker can only
            # claim after this generation-scoped receipt exists.
            _write_new(path / "activated.request", {
                "schema": "ops-disposable-pair-ready/1",
                "generation": generation, "manifest_sha256": _digest(manifest),
            })
            return proof
        except Exception:
            # No exception text, argv, environment, or passwords in receipts.
            sealed: dict[str, str] = {}
            blocked: list[str] = []
            for name, ctl in (("api", self.api), ("worker", self.worker)):
                if generation not in ctl.handles:
                    continue
                if generation not in ctl.proofs:
                    blocked.append(name)  # child exists but no trusted birth proof
                    continue
                try:
                    receipt = ctl.stop_and_seal(generation, expected=ctl.proofs[generation])
                    sealed[name] = _digest(receipt)
                except Exception:
                    blocked.append(name)  # leave alive, never force terminate
            _write_new(path / "rollback.json", {
                "schema": "ops-disposable-pair-rollback/1",
                "generation": generation,
                "status": "BLOCKED" if blocked else "SEALED_PARTIAL",
                "sealed_children": sealed, "unverified_children": blocked,
                "restart_permitted": False,
            })
            raise LifecycleBlocked("Pair startup failed; reserved generation cannot be reused") from None

    def assert_both_owned(self, proof: PairProof) -> None:
        if self.pairs.get(proof.generation) != proof:
            raise LifecycleBlocked("Caller supplied another API/worker pair")
        observe_owned(self.api.handles[proof.generation], proof.api)
        observe_owned(self.worker.handles[proof.generation], proof.worker)

    def stop_pair_and_seal(self, proof: PairProof, *, timeout: float = 8.0) -> dict:
        # Both independently attested alive before either stop request is created.
        self.assert_both_owned(proof)
        g = proof.generation
        path = self.pair_root / f"{g:04d}"
        if (path / "closed.json").exists():
            raise LifecycleBlocked("Pair already closed")
        manifest = _load(path / "manifest.json")
        ready = _load(path / "activated.request")
        previous = (_digest(_load(self.pair_root / f"{g - 1:04d}" / "closed.json"))
                    if g else None)
        if (manifest.get("schema") != "ops-disposable-pair/1"
                or manifest.get("generation") != g
                or manifest.get("root") != str(self.root)
                or manifest.get("previous_closure_sha256") != previous
                or ready != {"schema": "ops-disposable-pair-ready/1",
                             "generation": g, "manifest_sha256": _digest(manifest)}
                or manifest.get("api") != json.loads(json.dumps(vars(proof.api)))
                or manifest.get("worker") != json.loads(json.dumps(vars(proof.worker)))):
            raise LifecycleBlocked("Pair manifest ownership changed")
        api_closed = self.api.stop_and_seal(g, expected=proof.api, timeout=timeout)
        worker_closed = self.worker.stop_and_seal(g, expected=proof.worker, timeout=timeout)
        receipt = {
            "schema": "ops-disposable-pair-closure/1", "generation": g,
            "manifest_sha256": _digest(manifest),
            "api_closure_sha256": _digest(api_closed),
            "worker_closure_sha256": _digest(worker_closed),
            "api_exit_code": 0, "worker_exit_code": 0,
        }
        _write_new(path / "closed.json", receipt)
        return receipt

    def sample_resources(self, proof: PairProof) -> dict:
        self.assert_both_owned(proof)
        return {
            "api": resource_receipt(self.api.handles[proof.generation], proof.api),
            "worker": resource_receipt(self.worker.handles[proof.generation], proof.worker),
            "postgresql": "NOT_RUN",
            "wal_bytes": None,
            "queue_depth": None,
            "unknown_fields": ["wal_bytes", "queue_depth"],
        }
