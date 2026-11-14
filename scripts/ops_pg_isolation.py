"""Exact CI-only PostgreSQL connection policy; reject libpq environment overrides.

Never use this module for a user database or a production application DSN.
"""
from __future__ import annotations
import os


CI_ENDPOINT = {
    "host": "127.0.0.1",
    "port": "55479",
    "dbname": "ops_isolated",
    "user": "ops_tester",
    "password": "isolated-only",
}


def require_isolated_dsn(dsn: str) -> str:
    """Refuse every unexpected connection parameter or libpq env override.

    A different hostaddr/service/options may silently redirect a connection
    even when host, port, user, and database appear correct.
    """
    if not isinstance(dsn, str) or not dsn:
        raise RuntimeError("REFUSED: missing disposable PostgreSQL DSN")
    if any(name.startswith("PG") for name in os.environ):
        raise RuntimeError("REFUSED: libpq environment overrides forbidden")
    from psycopg.conninfo import conninfo_to_dict
    try:
        values = conninfo_to_dict(dsn)
    except Exception as exc:
        raise RuntimeError("REFUSED: malformed disposable PostgreSQL DSN") from exc
    if values != CI_ENDPOINT:
        raise RuntimeError("REFUSED: non-disposable PostgreSQL endpoint")
    return dsn
