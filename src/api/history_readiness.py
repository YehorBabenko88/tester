"""Explicit historical source readiness contract for an isolated Tester application DB.

Does not create schemas or start workers. The installer must provision a distinct
application database and run existing platform/storage/lab migrations first.
"""

from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from src.api.catalog_store import validate_catalog_dsn
from src.api.db import connect


def validate_history_dsn(app_dsn: str, catalog_dsn: str) -> str:
    """Reject the shared/default cluster, privileged catalog DB, or unsafe routing."""
    validate_catalog_dsn(catalog_dsn)
    a = conninfo_to_dict(app_dsn)
    c = conninfo_to_dict(catalog_dsn)
    host = a.get("host")
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("Historical app DB must be loopback-only")
    if a.get("hostaddr", "127.0.0.1") != "127.0.0.1":
        raise ValueError("Historical app DB must pin numeric loopback")
    if a.get("port") != "55473":
        raise ValueError("Historical app DB must use isolated Preview PostgreSQL 55473")
    if a.get("dbname") in (None, "", "postgres", "template0", "template1", c.get("dbname")):
        raise ValueError("Historical app DB must be separate from catalog and templates")
    if a.get("user") in (None, "", "postgres", c.get("user")):
        raise ValueError("Historical app DB must use dedicated non-superuser role")
    if not a.get("password"):
        raise ValueError("Historical app DB requires a credential")
    if set(a) - {"host", "hostaddr", "port", "dbname", "user", "password", "sslmode"}:
        raise ValueError("Unsupported application DSN parameters")
    return app_dsn


def check_history_schema(app_dsn: str, catalog_dsn: str) -> dict:
    """Read-only readiness; never auto-migrate or claim that a worker is running."""
    dsn = validate_history_dsn(app_dsn, catalog_dsn)
    with connect(dsn) as conn:
        for table in (
            "tester_platform.jobs",
            "tester_platform.workers",
            "tester_storage.schema_migrations",
            "tester_lab.projections",
        ):
            schema, name = table.split(".", 1)
            conn.execute(
                sql.SQL("SELECT 1 FROM {} LIMIT 0").format(
                    sql.Identifier(schema, name)
                )
            )
    return {
        "app_database": "SCHEMA_READABLE",
        "worker": "NOT_VERIFIED",
        "historical_coverage": "UNKNOWN",
    }
