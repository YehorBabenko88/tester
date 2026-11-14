"""Opt-in portable lifecycle primitives; no force-kill, UAC, reboot or SCM calls."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Callable, Iterable

class OwnershipError(RuntimeError):
    pass

class CheckpointError(RuntimeError):
    pass

def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()

def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=".checkpoint-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(_canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

@dataclass(frozen=True)
class Ownership:
    instance_id: str
    pid: int
    start_token: str
    root: str

    def assert_matches(self, observed: "Ownership") -> None:
        if not self.instance_id or self != observed:
            raise OwnershipError("Process ownership mismatch; refusing lifecycle action")

def write_checkpoint(path: Path, owner: Ownership, jobs: Iterable[dict], generation: int) -> None:
    """Persist durable job identities; never serialize arbitrary executable commands."""
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
        raise CheckpointError("Negative generation")
    rows = list(jobs)
    if any(not isinstance(row, dict) or set(row) != {"id", "state"} for row in rows):
        raise CheckpointError("Only job id and state may be persisted")
    ids = [row.get("id") for row in rows]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise CheckpointError("Job IDs must be unique nonempty strings")
    if any(row.get("state") not in ("queued", "running", "completed", "failed") for row in rows):
        raise CheckpointError("Invalid job state")
    body = {"schema": 1, "owner": vars(owner), "generation": generation,
            "saved_utc": datetime.now(timezone.utc).isoformat(), "jobs": rows}
    _atomic_json(path, {"body": body, "sha256": hashlib.sha256(_canonical(body)).hexdigest()})

def load_checkpoint(path: Path, expected_instance_id: str) -> dict:
    try:
        envelope = json.loads(path.read_bytes())
        if not isinstance(envelope, dict) or set(envelope) != {"body", "sha256"}:
            raise CheckpointError("Invalid checkpoint envelope")
        body = envelope["body"]
        if not isinstance(body, dict) or set(body) != {"schema", "owner", "generation", "saved_utc", "jobs"}:
            raise CheckpointError("Invalid checkpoint body")
        if not isinstance(body["jobs"], list) or len(body["jobs"]) > 100000:
            raise CheckpointError("Invalid job count")
        if any(not isinstance(row, dict) or set(row) != {"id", "state"} or
               not isinstance(row["id"], str) or not row["id"] or
               row["state"] not in ("queued", "running", "completed", "failed")
               for row in body["jobs"]):
            raise CheckpointError("Invalid checkpoint job")
        if not isinstance(body["generation"], int) or isinstance(body["generation"], bool):
            raise CheckpointError("Invalid generation")
        if not isinstance(body["owner"], dict) or set(body["owner"]) != {"instance_id", "pid", "start_token", "root"}:
            raise CheckpointError("Invalid checkpoint owner")
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise CheckpointError("Malformed checkpoint") from exc
    if hashlib.sha256(_canonical(body)).hexdigest() != envelope["sha256"]:
        raise CheckpointError("Checkpoint digest mismatch")
    if body["schema"] != 1 or body["owner"]["instance_id"] != expected_instance_id:
        raise CheckpointError("Checkpoint schema or instance mismatch")
    if body["generation"] < 0:
        raise CheckpointError("Invalid checkpoint generation")
    ids = [row["id"] for row in body["jobs"]]
    if len(set(ids)) != len(ids):
        raise CheckpointError("Duplicate job IDs")
    return body

def resume_plan(body: dict, durable_completed: set[str]) -> list[str]:
    """Reconcile durable completions before requeue; database writes must be idempotent."""
    return [row["id"] for row in body["jobs"]
            if row["state"] in ("queued", "running") and row["id"] not in durable_completed]

def request_cooperative_stop(expected: Ownership, observed: Ownership,
                             signal_stop: Callable[[], None]) -> None:
    expected.assert_matches(observed)
    signal_stop()

def resource_sample(pid: int, process_lookup: Callable[[int], object]) -> dict:
    """Read-only psutil-compatible sampler, with injected process lookup."""
    process = process_lookup(pid)
    mem = process.memory_info()
    return {"pid": pid, "rss_bytes": int(mem.rss),
            "cpu_percent": float(process.cpu_percent(interval=None)),
            "threads": int(process.num_threads()),
            "sampled_utc": datetime.now(timezone.utc).isoformat()}
