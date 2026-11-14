"""Opt-in CI-only child for disposable PostgreSQL lifecycle acceptance.

No network beyond the exact isolated test database; must never run on PC4.
"""
import json
import os
from pathlib import Path
import sys
import time
from uuid import UUID

from scripts.ops_pg_isolation import require_isolated_dsn
from src.api.jobs import JobQueue


def main() -> int:
    dsn = os.environ.get("TEST_OPS_ISOLATED_DSN", "")
    try:
        require_isolated_dsn(dsn)
    except RuntimeError:
        return 91
    job_id = UUID(os.environ["OPS_JOB_ID"])
    generation = int(os.environ["OPS_GENERATION"])
    stop = Path(os.environ["OPS_STOP_PATH"])
    output = Path(os.environ["OPS_CHECKPOINT_PATH"])
    if generation not in (0, 1) or stop.parent != output.parent:
        return 92
    # Never claim a durable job until the controller has enrolled BOTH API and
    # worker and atomically published their exact pair manifest receipt.
    ready = Path(os.environ.get("OPS_PAIR_READY_PATH", ""))
    if (ready.name != "activated.request" or ready.parent.name != f"{generation:04d}"
            or ready.parent.parent.name != "pair-generations"):
        return 95
    until = time.monotonic() + 12
    while not ready.is_file():
        if stop.is_file():
            # Startup failed: cooperative exit, no DB transaction or job claim.
            with output.open("x", encoding="utf-8") as handle:
                json.dump({"generation": generation,
                           "owner_marker": os.environ["OPS_OWNER_MARKER"],
                           "inflight": "not-admitted", "durable_state": "unchanged"}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            return 0
        if time.monotonic() > until:
            return 96
        time.sleep(0.02)
    if stop.is_file():
        return 97  # no unowned DB claim on race with stop request
    from scripts.ops_disposable_lifecycle import _digest
    try:
        manifest = json.loads((ready.parent / "manifest.json").read_text(encoding="utf-8"))
        receipt = json.loads(ready.read_text(encoding="utf-8"))
        if receipt != {"schema": "ops-disposable-pair-ready/1",
                       "generation": generation, "manifest_sha256": _digest(manifest)}:
            return 98
    except (OSError, ValueError):
        return 99
    queue = JobQueue(dsn, lease_seconds=30)
    lease = queue.claim()
    if lease is None or lease.id != job_id:
        return 93
    until = time.monotonic() + 12
    while not stop.is_file():
        if time.monotonic() > until:
            return 94
        time.sleep(0.02)
    queue.checkpoint(job_id, lease.lease_token, {"generation": generation}, 0.5)
    if generation == 0:
        queue.release(job_id, lease.lease_token)
    else:
        queue.finish(job_id, lease.lease_token)
    with output.open("x", encoding="utf-8") as handle:
        json.dump({"generation": generation, "owner_marker": os.environ["OPS_OWNER_MARKER"],
                   "inflight": str(job_id), "durable_state": "queued" if generation == 0 else "completed"}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    return 0


if __name__ == "__main__":
    sys.exit(main())
