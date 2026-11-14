"""Read-only PostgreSQL job-state evidence for OPS recovery preflight.

No schema migration, lease mutation or automatic restart.
"""
from __future__ import annotations

from uuid import UUID

from psycopg.rows import dict_row

from src.api.db import connect

class SnapshotRefusal(RuntimeError):
    pass

def durable_job_snapshot(dsn: str, job_ids: list[str]) -> tuple[dict[str, str], frozenset[str]]:
    """Read states and expiry in one REPEATABLE READ snapshot.

    Expiry is evaluated by PostgreSQL's clock, not a local workstation clock.
    The snapshot is advisory only: final lease claims MUST still fence in SQL.
    """
    if not dsn or not job_ids or len(job_ids) > 100000:
        raise SnapshotRefusal("Missing DSN or invalid job count")
    try:
        parsed = [UUID(value) for value in job_ids]
    except (ValueError, TypeError, AttributeError) as exc:
        raise SnapshotRefusal("Invalid job UUID") from exc
    if len(set(parsed)) != len(parsed):
        raise SnapshotRefusal("Duplicate job UUID")
    with connect(dsn) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """SELECT id, state, (state='running' AND lease_until < now())
                    AS expired FROM tester_platform.jobs WHERE id = ANY(%s)""",
                    (parsed,),
                )
                rows = cur.fetchall()
    states = {str(row["id"]): row["state"] for row in rows}
    expired = frozenset(str(row["id"]) for row in rows if row["expired"])
    if len(states) != len(parsed):
        raise SnapshotRefusal("Missing durable job")
    if any(state not in {"queued", "running", "completed", "failed", "paused"} for state in states.values()):
        raise SnapshotRefusal("Unknown durable state")
    return states, expired
