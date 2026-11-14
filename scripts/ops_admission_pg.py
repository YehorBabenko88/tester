"""Actual isolated PG incarnation + lease-state gate; no PC4 or PG5432."""
from __future__ import annotations

from scripts.ops_pg_isolation import require_isolated_dsn


def read_admission_pg(dsn: str, *, connector=None) -> dict:
    """Read-only identity and state from exact disposable PG16 endpoint."""
    require_isolated_dsn(dsn)
    if connector is None:
        from src.api.db import connect
        connector = connect
    with connector(dsn) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        row = conn.execute(
            "SELECT (pg_control_system()).system_identifier::text,"
            "pg_postmaster_start_time()"
        ).fetchone()
        if not row or row[0] is None or row[1] is None:
            raise RuntimeError("PostgreSQL incarnation evidence unavailable")
        states = conn.execute(
            "SELECT state,count(*) FROM tester_platform.jobs GROUP BY state"
        ).fetchall()
    if any(state not in ("queued", "running", "paused", "completed", "failed")
           for state, _ in states):
        raise RuntimeError("Unrecognized durable queue state")
    return {
        "pg_system_identifier": str(row[0]),
        "pg_postmaster_start": row[1].isoformat(),
        "states": {state: int(count) for state, count in states},
        "active_running": sum(int(count) for state, count in states if state == "running"),
    }


def safe_lease_states(snapshot: dict) -> dict[str, str]:
    """Fail closed on all running leases; never mutate or reclaim them."""
    if snapshot.get("active_running") != 0:
        return {"running": "running"}
    return {state: state for state in snapshot.get("states", {})}
