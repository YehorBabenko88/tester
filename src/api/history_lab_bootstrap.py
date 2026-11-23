"""Explicit lab projection table bootstrap for a new, dedicated Preview history DB.

Only run after platform and storage migrations in a new isolated DB. The table
is required by the existing CatalogHandler's fenced transaction.
"""

import hashlib

from src.api.db import connect
from src.api.history_readiness import validate_history_dsn

PROJECTION_SQL = (
    "CREATE TABLE tester_lab.projections ("
    "record_key text PRIMARY KEY REFERENCES tester_storage.raw_records(record_key),"
    "record jsonb NOT NULL)"
)


def migrate_release_projections(conn):
    """Canonical release migration on an already isolated owner connection.

    Legacy Preview bootstrap keeps its strict DSN and existing-table refusal.
    A release can repeat only its own checksum-enrolled projection migration.
    """
    conn.execute("SELECT pg_advisory_xact_lock(725281410)")
    if conn.execute("SELECT to_regclass('tester_storage.raw_records')").fetchone()[0] is None:
        raise RuntimeError("Storage migration missing; refusing lab bootstrap")
    enrolled = conn.execute("SELECT to_regclass('tester_lab.release_migrations')").fetchone()[0]
    digest = hashlib.sha256(PROJECTION_SQL.encode()).hexdigest()
    if enrolled is not None:
        previous = conn.execute(
            "SELECT checksum FROM tester_lab.release_migrations WHERE version='projections/v1'"
        ).fetchone()
        if previous != (digest,):
            raise RuntimeError("Release projection migration checksum mismatch")
        conn.execute("SELECT record_key,record FROM tester_lab.projections LIMIT 0")
        return
    if conn.execute("SELECT to_regclass('tester_lab.projections')").fetchone()[0] is not None:
        raise RuntimeError("Refusing unregistered existing projection table")
    conn.execute("CREATE SCHEMA IF NOT EXISTS tester_lab")
    conn.execute(
        "CREATE TABLE tester_lab.release_migrations (version text PRIMARY KEY, checksum text NOT NULL)"
    )
    conn.execute(PROJECTION_SQL)
    conn.execute(
        "INSERT INTO tester_lab.release_migrations VALUES ('projections/v1',%s)", (digest,)
    )


def migrate_lab_projections(app_dsn: str, catalog_dsn: str) -> None:
    validate_history_dsn(app_dsn, catalog_dsn)
    with connect(app_dsn) as conn:
        with conn.transaction():
            # Never mutate an existing lab schema: a foreign/older schema
            # must be reviewed instead of silently adopted or overwritten.
            exists = conn.execute("SELECT to_regclass('tester_lab.projections')").fetchone()[0]
            if exists is not None:
                raise RuntimeError(
                    "Lab projection table already exists; manual schema audit required"
                )
            raw = conn.execute("SELECT to_regclass('tester_storage.raw_records')").fetchone()[0]
            if raw is None:
                raise RuntimeError("Storage migration missing; refusing lab bootstrap")
            conn.execute("CREATE SCHEMA IF NOT EXISTS tester_lab")
            conn.execute(PROJECTION_SQL)
