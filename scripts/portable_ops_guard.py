"""Fail-closed, opt-in lifecycle coordinator for disposable portable runtimes.

This is not an adapter for the existing PC4 runner. A verified external observer
must supply birth-bound process handles and durable database snapshots.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from scripts.portable_ops import Ownership, OwnershipError, CheckpointError, load_checkpoint, resume_plan

class LifecycleRefusal(RuntimeError):
    pass

@dataclass(frozen=True)
class ProcessWitness:
    ownership: Ownership
    image: str
    argv: tuple[str, ...]
    exited: bool
    exit_code: int | None
    drain_complete: bool

    def require_clean_exit(self, expected: Ownership, image: str, argv: tuple[str, ...]) -> None:
        expected.assert_matches(self.ownership)
        if self.image != image or self.argv != argv:
            raise LifecycleRefusal("Executable or argv mismatch")
        if not self.exited or self.exit_code != 0 or not self.drain_complete:
            raise LifecycleRefusal("No externally observed clean exit and drain")

def guarded_resume(
    checkpoint: Path,
    expected_owner: Ownership,
    witness: ProcessWitness,
    expected_image: str,
    expected_argv: tuple[str, ...],
    durable_states: Mapping[str, str],
    claim_job: Callable[[str], object | None],
    expired_running: frozenset[str] = frozenset(),
) -> list[object]:
    """Reconcile only with a transactional, single-winner durable claim callback.

    claim_job must atomically lease the named job from the authoritative database,
    returning None if already claimed/finished. Successful claims must carry
    their lease token; the caller must hand them to a fenced worker. No spawn.
    """
    witness.require_clean_exit(expected_owner, expected_image, expected_argv)
    body = load_checkpoint(checkpoint, expected_owner.instance_id)
    if body["owner"] != vars(expected_owner):
        raise OwnershipError("Checkpoint owner changed")
    valid = {"queued", "running", "completed", "failed", "paused"}
    if any(state not in valid for state in durable_states.values()):
        raise CheckpointError("Unrecognized durable state")
    checkpoint_ids = {job["id"] for job in body["jobs"]}
    if not checkpoint_ids.issubset(durable_states):
        raise LifecycleRefusal("Missing authoritative durable job state")
    completed = {key for key, value in durable_states.items() if value == "completed"}
    candidates = resume_plan(body, completed)
    # A failed job requires explicit retry authorization; never auto-requeue it.
    # Never reclaim a live lease. The caller must prove lease expiry in the DB.
    running = {key for key in candidates if durable_states[key] == "running"}
    if not running.issubset(expired_running):
        raise LifecycleRefusal("Running lease not proven expired")
    selected = [key for key in candidates if durable_states[key] == "queued" or key in running]
    # All preflight checks above run before any mutating claim callback.
    claimed = []
    for key in selected:
        lease = claim_job(key)
        if lease is not None:
            if not getattr(lease, "lease_token", None) or str(getattr(lease, "id", "")) != key:
                raise LifecycleRefusal("Claim returned wrong identity or missing fencing token")
            claimed.append(lease)
    return claimed

@dataclass(frozen=True)
class ResourcePolicy:
    max_rss_bytes: int
    max_cpu_percent: float
    max_threads: int

    def evaluate(self, sample: dict) -> tuple[str, ...]:
        if self.max_rss_bytes <= 0 or self.max_cpu_percent <= 0 or self.max_threads <= 0:
            raise ValueError("Resource limits must be positive")
        if sample["rss_bytes"] < 0 or sample["cpu_percent"] < 0 or sample["threads"] < 0:
            raise ValueError("Invalid resource observation")
        violations = []
        if sample["rss_bytes"] > self.max_rss_bytes:
            violations.append("RSS")
        if sample["cpu_percent"] > self.max_cpu_percent:
            violations.append("CPU")
        if sample["threads"] > self.max_threads:
            violations.append("THREADS")
        return tuple(violations)

def sustained_metrics(samples: list[dict], expected_symbols: set[str], minimum_samples: int) -> dict:
    """Fixture/real sampler aggregation. Never calls the exchange or PostgreSQL."""
    if len(expected_symbols) != 10 or len(samples) < minimum_samples or minimum_samples <= 0:
        raise LifecycleRefusal("Ten symbols and sufficient samples required")
    if {row["symbol"] for row in samples} != expected_symbols:
        raise LifecycleRefusal("Symbol coverage incomplete or unexpected")
    for row in samples:
        if row["duration_ms"] < 0 or row["rss_bytes"] < 0 or row["cpu_percent"] < 0:
            raise LifecycleRefusal("Invalid observation")
    latencies = sorted(row["duration_ms"] for row in samples)
    def percentile(q: float) -> float:
        return latencies[min(len(latencies)-1, int((len(latencies)-1)*q))]
    return {"symbols": len(expected_symbols), "samples": len(samples),
            "p50_ms": percentile(.50), "p95_ms": percentile(.95),
            "max_rss_bytes": max(row["rss_bytes"] for row in samples),
            "max_cpu_percent": max(row["cpu_percent"] for row in samples)}
