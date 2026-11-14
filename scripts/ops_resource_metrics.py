"""Read-only PostgreSQL WAL/queue metrics and birth-bound process resource receipts.

The hard-coded isolated-DSN guard is mandatory. The module does not stop
processes or mutate PostgreSQL and never turns unknown metrics into zeros.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from scripts.ops_pg_isolation import require_isolated_dsn


_STATES = ("queued", "running", "paused", "completed", "failed")


def collect_pg_metrics(dsn: str, *, connector: Callable | None = None,
                       guard: Callable[[str], str] = require_isolated_dsn) -> dict:
    """Return separately sampled PostgreSQL WAL and durable queue measurements.

    WAL_BYTES is the cumulative postmaster pg_stat_wal counter, not a sample
    of bytes physically written during a benchmark. To derive growth, compare
    snapshots from the same exact PostgreSQL incarnation.
    """
    guard(dsn)
    if connector is None:
        from src.api.db import connect
        connector = connect
    metrics = {
        "status": "PARTIAL", "sampled_utc": datetime.now(timezone.utc).isoformat(),
        "queue": {state: None for state in _STATES}, "queue_depth": None,
        "wal_bytes": None, "pg_system_identifier": None,
        "pg_postmaster_start": None, "wal_delta_bytes": None,
        "unknown_fields": [],
    }
    try:
        with connector(dsn) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            row = conn.execute(
                "SELECT count(*) FILTER (WHERE state='queued'),"
                "count(*) FILTER (WHERE state='running'),"
                "count(*) FILTER (WHERE state='paused'),"
                "count(*) FILTER (WHERE state='completed'),"
                "count(*) FILTER (WHERE state='failed')"
                " FROM tester_platform.jobs"
            ).fetchone()
            if row is None or len(row) != len(_STATES):
                raise RuntimeError("Incomplete queue evidence")
            values = [int(value) for value in row]
            if any(value < 0 for value in values):
                raise RuntimeError("Invalid queue counters")
            metrics["queue"] = dict(zip(_STATES, values))
            metrics["queue_depth"] = sum(metrics["queue"][key]
                                         for key in ("queued", "running", "paused"))
    except Exception:
        pass  # unknown counts remain NULL; connection already closed
    try:
        with connector(dsn) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            row = conn.execute(
                "SELECT wal_bytes FROM pg_stat_wal"
            ).fetchone()
            if row is not None and row[0] is not None:
                value = int(row[0])
                if value >= 0:
                    metrics["wal_bytes"] = value
            incarnation = conn.execute(
                "SELECT (pg_control_system()).system_identifier::text,"
                "pg_postmaster_start_time()"
            ).fetchone()
            if incarnation and incarnation[0] and incarnation[1]:
                metrics["pg_system_identifier"] = str(incarnation[0])
                metrics["pg_postmaster_start"] = incarnation[1].isoformat()
    except Exception:
        # Retain independently observed queue data, other fields unknown.
        pass
    metrics["unknown_fields"] = [key for key in
        ("queue_depth", "wal_bytes", "pg_system_identifier", "pg_postmaster_start", "wal_delta_bytes")
        if metrics[key] is None]
    if metrics["queue_depth"] is not None and metrics["wal_bytes"] is not None:
        metrics["status"] = "OBSERVED"
    return metrics


def wal_growth(before: dict, after: dict) -> dict:
    """Fail closed on PG restart, counter reset or unavailable WAL telemetry."""
    identity = ("pg_system_identifier", "pg_postmaster_start")
    if any(before.get(key) is None or after.get(key) is None
           or before[key] != after[key] for key in identity):
        return {"status": "UNKNOWN_INCARNATION", "wal_delta_bytes": None}
    old, new = before.get("wal_bytes"), after.get("wal_bytes")
    if old is None or new is None or new < old:
        return {"status": "UNKNOWN_WAL_COUNTER", "wal_delta_bytes": None}
    return {"status": "OBSERVED", "wal_delta_bytes": new - old}
