"""Atomic, opt-in claim of an already admitted job for portable recovery.

Uses the same tester_platform.jobs/control tables and lease fencing as JobQueue.
No schema changes, automatic retries, process signals, or service actions.
"""
from __future__ import annotations

from uuid import UUID, uuid4

from psycopg.rows import dict_row
from src.api.models import Lease
from src.api.db import connect


class ClaimRefusal(RuntimeError):
    pass


def claim_existing(dsn: str, job_id: str, lease_seconds: int = 60) -> Lease | None:
    """Single-winner claim; never steal an unexpired lease or a paused job.

    None means the job is not claimable. A successful claim returns the FULL
    lease, including its fencing token and checkpoint; callers must retain it.
    """
    if not dsn or not 1 <= lease_seconds <= 3600:
        raise ClaimRefusal("Invalid recovery configuration")
    try:
        uid = UUID(job_id)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ClaimRefusal("Invalid job ID") from exc
    with connect(dsn) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM tester_platform.control WHERE singleton FOR UPDATE")
        control = cur.fetchone()
        if not control or control["draining"]:
            return None
        cur.execute(
            "SELECT count(*) AS active FROM tester_platform.jobs "
            "WHERE state='running' AND lease_until>now()"
        )
        if cur.fetchone()["active"] >= control["max_running"]:
            return None
        cur.execute(
            """UPDATE tester_platform.jobs
               SET state='running', attempts=attempts+1, lease_token=%s,
                   lease_until=now()+(%s * interval '1 second'), updated_at=now()
               WHERE id=%s AND
                 (state='queued' OR (state='running' AND lease_until < now()))
               RETURNING *""",
            (uuid4(), lease_seconds, uid),
        )
        row = cur.fetchone()
        return Lease.model_validate(row) if row is not None else None
