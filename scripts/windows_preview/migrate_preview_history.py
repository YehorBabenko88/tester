"""Explicit, operator-run migration of a dedicated Tester Preview history database.

Never creates users/databases or contacts the default PostgreSQL port.
Run only after provisioning tester_preview_history with a dedicated role.
"""
import os

from src.api.db import connect, migrate as migrate_platform
from src.api.history_readiness import check_history_schema, validate_history_dsn
from src.storage.migrations import migrate as migrate_storage


def migrate_preview_history(app_dsn: str, catalog_dsn: str) -> dict:
    validate_history_dsn(app_dsn, catalog_dsn)
    # Migrations are explicit and checksummed; never run on API/worker startup.
    migrate_platform(app_dsn)
    with connect(app_dsn) as conn:
        migrate_storage(conn)
    # The lab projection schema has a separate migration owner and is NOT
    # synthesized here. Readiness remains blocked until that owner migrates it.
    return check_history_schema(app_dsn, catalog_dsn)


if __name__ == "__main__":
    app = os.environ.get("TESTER_DATABASE_URL")
    catalog = os.environ.get("TESTER_MARKET_CATALOG_DATABASE_URL")
    if not app or not catalog:
        raise SystemExit("Explicit TESTER_DATABASE_URL and TESTER_MARKET_CATALOG_DATABASE_URL required")
    print(migrate_preview_history(app, catalog))
