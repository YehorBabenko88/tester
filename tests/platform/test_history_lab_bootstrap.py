from unittest.mock import MagicMock, patch

import pytest

from src.api.history_lab_bootstrap import migrate_lab_projections

CATALOG = "host=127.0.0.1 hostaddr=127.0.0.1 port=55473 dbname=tester_preview_catalog user=tester_preview_app password=secret"
APP = "host=127.0.0.1 hostaddr=127.0.0.1 port=55473 dbname=tester_preview_history user=tester_preview_history_app password=secret"


def test_refuses_existing_lab_table_without_ddl():
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = ("tester_lab.projections",)
    with patch("src.api.history_lab_bootstrap.connect", return_value=conn):
        with pytest.raises(RuntimeError, match="already exists"):
            migrate_lab_projections(APP, CATALOG)
    assert all("CREATE " not in call.args[0] for call in conn.execute.call_args_list)


def test_refuses_missing_storage_before_ddl():
    conn = MagicMock()
    conn.execute.return_value.fetchone.side_effect = [(None,), (None,)]
    with patch("src.api.history_lab_bootstrap.connect", return_value=conn):
        with pytest.raises(RuntimeError, match="Storage migration missing"):
            migrate_lab_projections(APP, CATALOG)
    assert all("CREATE " not in call.args[0] for call in conn.execute.call_args_list)


def test_creates_only_projection_table_in_fresh_history_db():
    conn = MagicMock()
    conn.execute.return_value.fetchone.side_effect = [(None,), ("tester_storage.raw_records",)]
    with patch("src.api.history_lab_bootstrap.connect", return_value=conn):
        migrate_lab_projections(APP, CATALOG)
    statements = [call.args[0] for call in conn.execute.call_args_list]
    assert sum("CREATE TABLE" in sql for sql in statements) == 1
    assert "REFERENCES tester_storage.raw_records(record_key)" in statements[-1]
    assert "jsonb NOT NULL" in statements[-1]
