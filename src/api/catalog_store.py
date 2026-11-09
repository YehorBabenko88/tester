"""Explicitly migrated catalog snapshots and session-owned category scheduling.

No constructor/startup path creates schema. Run this module as an operator action.
"""

import hashlib
import threading
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

from .catalog_history import (
    MAX_POINTS_PER_CATEGORY,
    RETENTION_MS,
    TOLERANCE_MS,
    enrich_snapshot,
    fresh_points,
)
from .db import connect

LOCKS = {"linear": 725281401, "inverse": 725281402}


def validate_catalog_dsn(dsn):
    values = conninfo_to_dict(dsn)
    port = values.get("port", "5432")
    host = values.get("host")
    pinned = "127.0.0.1" if host == "localhost" else host
    allowed = {"host", "hostaddr", "port", "dbname", "user", "password", "sslmode", "sslrootcert"}
    if (
        host not in {"127.0.0.1", "localhost", "::1"}
        or set(values) - allowed
        or values.get("hostaddr", pinned) != pinned
        or not port.isdecimal()
        or not 1 <= int(port) <= 65535
        or int(port) == 5432
        or not values.get("dbname")
        or values["dbname"] in {"postgres", "template0", "template1", "tester_lab", "tester_native"}
    ):
        raise ValueError("Catalog requires an explicit dedicated loopback non-5432 database")
    # Pin numeric address in the actual DSN handed to every connection boundary.
    # libpq PGHOSTADDR / PGSERVICE cannot redirect an otherwise validated host.
    return make_conninfo(**{**values, "host": pinned, "hostaddr": pinned, "port": str(int(port))})


def migrate_catalog(dsn):
    dsn = validate_catalog_dsn(dsn)
    with connect(dsn) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(725281400)")
        conn.execute("CREATE SCHEMA IF NOT EXISTS tester_catalog")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS tester_catalog.migrations "
            "(version text PRIMARY KEY, checksum text NOT NULL)"
        )
        for path in sorted(Path(__file__).with_name("catalog_migrations").glob("*.sql")):
            content = path.read_text(encoding="utf-8")
            digest = hashlib.sha256(content.encode()).hexdigest()
            previous = conn.execute(
                "SELECT checksum FROM tester_catalog.migrations WHERE version=%s", (path.name,)
            ).fetchone()
            if previous:
                if previous[0] != digest:
                    raise RuntimeError("Catalog migration checksum mismatch")
                continue
            conn.execute(content)
            conn.execute(
                "INSERT INTO tester_catalog.migrations VALUES (%s,%s)", (path.name, digest)
            )


class CatalogStore:
    def __init__(self, dsn):
        self.dsn = validate_catalog_dsn(dsn)
        self._owners = {}
        self._lock = threading.RLock()

    def check_ready(self):
        with connect(self.dsn) as conn:
            conn.execute(
                "SELECT category,symbol,source_time_ms,metrics "
                "FROM tester_catalog.ticker_observations LIMIT 0"
            )

    def claim(self, category):
        key = LOCKS[category]
        with self._lock:
            previous = self._owners.get(category)
            if previous is not None:
                try:
                    previous.execute("SELECT 1")
                    return True
                except psycopg.Error:
                    previous.close()
                    del self._owners[category]
            conn = connect(self.dsn)
            conn.autocommit = True
            try:
                if not conn.execute("SELECT pg_try_advisory_lock(%s)", (key,)).fetchone()[0]:
                    conn.close()
                    return False
                self._owners[category] = conn
                return True
            except BaseException:
                conn.close()
                raise

    def release(self, category):
        with self._lock:
            conn = self._owners.pop(category, None)
            if conn is not None:
                conn.close()

    def close(self):
        for category in LOCKS:
            self.release(category)

    def read(self, category):
        LOCKS[category]
        with connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT snapshot,attempt,pending,next_refresh_at,eligible_at,failures "
                "FROM tester_catalog.snapshots WHERE category=%s",
                (category,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Explicit catalog migration is required")
        return dict(
            zip(
                ("snapshot", "attempt", "pending", "next_refresh_at", "eligible_at", "failures"),
                row,
            ),
            category=category,
        )

    def request(self, category):
        LOCKS[category]
        with connect(self.dsn) as conn:
            result = conn.execute(
                "UPDATE tester_catalog.snapshots SET pending=true WHERE category=%s", (category,)
            )
            if result.rowcount != 1:
                raise RuntimeError("Explicit catalog migration is required")

    def take_pending(self, category):
        with self._lock:
            conn = self._owner(category)
            with conn.transaction():
                pending = conn.execute(
                    "SELECT pending FROM tester_catalog.snapshots WHERE category=%s FOR UPDATE",
                    (category,),
                ).fetchone()[0]
                conn.execute(
                    "UPDATE tester_catalog.snapshots SET pending=false WHERE category=%s",
                    (category,),
                )
                return pending

    def _owner(self, category):
        conn = self._owners.get(category)
        if conn is None or conn.closed:
            raise RuntimeError("Category scheduler ownership required")
        # A dedicated connection owns this lock for its whole lifetime; transactions
        # below cannot publish after connection loss or replacement.
        conn.execute("SELECT 1")
        return conn

    def publish(self, category, snapshot, attempt, next_refresh_at, eligible_at, failures):
        with self._lock:
            conn = self._owner(category)
            with conn.transaction():
                if snapshot is not None:
                    self._observe(conn, category, snapshot, attempt["at"])
                conn.execute(
                    "UPDATE tester_catalog.snapshots SET snapshot=COALESCE(%s,snapshot), "
                    "attempt=%s,next_refresh_at=%s,eligible_at=%s,failures=%s WHERE category=%s",
                    (
                        Jsonb(snapshot) if snapshot is not None else None,
                        Jsonb(attempt),
                        next_refresh_at,
                        eligible_at,
                        failures,
                        category,
                    ),
                )

    def _observe(self, conn, category, snapshot, now):
        points = fresh_points(snapshot, now)
        if points:
            with conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO tester_catalog.ticker_observations(category,symbol,source_time_ms,metrics) "
                    "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    [
                        (
                            category,
                            point["symbol"],
                            point["source_time_ms"],
                            Jsonb(point["metrics"]),
                        )
                        for point in points
                    ],
                )
        # Only this feature's rows are pruned, inside the same publication transaction.
        conn.execute(
            "DELETE FROM tester_catalog.ticker_observations WHERE category=%s "
            "AND source_time_ms < %s",
            (category, int(now * 1000) - RETENTION_MS),
        )
        conn.execute(
            "DELETE FROM tester_catalog.ticker_observations WHERE (category,symbol,source_time_ms) IN "
            "(SELECT category,symbol,source_time_ms FROM tester_catalog.ticker_observations "
            "WHERE category=%s ORDER BY source_time_ms DESC,symbol OFFSET %s)",
            (category, MAX_POINTS_PER_CATEGORY),
        )
        baselines = {}
        if points:
            rows = conn.execute(
                """
                SELECT c.symbol,w.window_ms,c.source_time_ms,w.metric,o.metrics->w.metric
                FROM jsonb_to_recordset(%s::jsonb) AS c(symbol text,source_time_ms bigint)
                CROSS JOIN (VALUES ('price',3600000::bigint),('open_interest',3600000::bigint),
                                   ('open_interest',86400000::bigint)) AS w(metric,window_ms)
                JOIN LATERAL (
                    SELECT metrics FROM tester_catalog.ticker_observations
                    WHERE category=%s AND symbol=c.symbol
                    AND metrics ? w.metric
                    AND source_time_ms BETWEEN c.source_time_ms-w.window_ms-%s
                                           AND c.source_time_ms-w.window_ms
                    ORDER BY source_time_ms DESC LIMIT 1
                ) AS o ON true
            """,
                (
                    Jsonb(
                        [
                            {"symbol": p["symbol"], "source_time_ms": p["source_time_ms"]}
                            for p in points
                        ]
                    ),
                    category,
                    TOLERANCE_MS,
                ),
            ).fetchall()
            for symbol, window, stamp, name, metric in rows:
                baselines.setdefault((symbol, window, stamp), {})[name] = metric
        enrich_snapshot(snapshot, baselines, now)


if __name__ == "__main__":
    from .config import load_settings

    settings = load_settings()
    if not settings.market_catalog_dsn:
        raise SystemExit("TESTER_MARKET_CATALOG_DATABASE_URL is required")
    migrate_catalog(settings.market_catalog_dsn)
