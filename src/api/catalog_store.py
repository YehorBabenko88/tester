"""Explicitly migrated catalog snapshots and session-owned category scheduling.

No constructor/startup path creates schema. Run this module as an operator action.
"""

import hashlib
import threading
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

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
    path = Path(__file__).with_name("catalog_migrations") / "001_catalog.sql"
    content = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(content.encode()).hexdigest()
    with connect(dsn) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(725281400)")
        conn.execute("CREATE SCHEMA IF NOT EXISTS tester_catalog")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS tester_catalog.migrations "
            "(version text PRIMARY KEY, checksum text NOT NULL)"
        )
        previous = conn.execute(
            "SELECT checksum FROM tester_catalog.migrations WHERE version=%s", (path.name,)
        ).fetchone()
        if previous:
            if previous[0] != digest:
                raise RuntimeError("Catalog migration checksum mismatch")
            return
        conn.execute(content)
        conn.execute("INSERT INTO tester_catalog.migrations VALUES (%s,%s)", (path.name, digest))


class CatalogStore:
    def __init__(self, dsn):
        self.dsn = validate_catalog_dsn(dsn)
        self._owners = {}
        self._lock = threading.RLock()

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


if __name__ == "__main__":
    from .config import load_settings

    settings = load_settings()
    if not settings.market_catalog_dsn:
        raise SystemExit("TESTER_MARKET_CATALOG_DATABASE_URL is required")
    migrate_catalog(settings.market_catalog_dsn)
