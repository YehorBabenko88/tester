"""Explicit lab projection table bootstrap for a new, dedicated Preview history DB.

Only run after platform and storage migrations in a new isolated DB. The table
is required by the existing CatalogHandler's fenced transaction.
"""

from src.api.db import connect
from src.api.history_readiness import validate_history_dsn


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
            conn.execute(
                "CREATE TABLE tester_lab.projections ("
                "record_key text PRIMARY KEY REFERENCES tester_storage.raw_records(record_key),"
                "record jsonb NOT NULL)"
            )
