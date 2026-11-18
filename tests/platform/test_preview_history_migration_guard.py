from unittest.mock import patch

import pytest

from scripts.windows_preview.migrate_preview_history import migrate_preview_history

CATALOG = (
    "host=127.0.0.1 hostaddr=127.0.0.1 port=55473 "
    "dbname=tester_preview_catalog user=tester_preview_app password=secret"
)
APP = (
    "host=127.0.0.1 hostaddr=127.0.0.1 port=55473 "
    "dbname=tester_preview_history user=tester_preview_history_app password=secret"
)


def test_rejects_default_port_before_any_migration():
    with patch("scripts.windows_preview.migrate_preview_history.migrate_platform") as migrate:
        with pytest.raises(ValueError):
            migrate_preview_history(APP.replace("port=55473", "port=5432"), CATALOG)
        migrate.assert_not_called()


def test_rejects_catalog_database_before_any_migration():
    with patch("scripts.windows_preview.migrate_preview_history.migrate_platform") as migrate:
        with pytest.raises(ValueError):
            migrate_preview_history(
                APP.replace("dbname=tester_preview_history", "dbname=tester_preview_catalog"),
                CATALOG,
            )
        migrate.assert_not_called()
