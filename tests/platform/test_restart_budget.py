"""Production admission cannot fall back to a mutable local budget journal."""

import json
from unittest.mock import MagicMock

import psycopg
import pytest

from src.api.restart_budget import BudgetDenied, DatabaseRestartBudget

IDENT = "00000000-0000-0000-0000-000000000042"
DSN = "host=127.0.0.1 port=55822 dbname=tester user=tester_app password=temporary"


def budget(tmp_path):
    return DatabaseRestartBudget(DSN, tmp_path, IDENT, "a" * 40)


@pytest.mark.parametrize(
    "dsn",
    [
        DSN.replace("55822", "5432"),
        DSN.replace("127.0.0.1", "192.0.2.1"),
        DSN + " options='-c search_path=public'",
        DSN.replace("tester_app", "postgres"),
        "malformed",
    ],
)
def test_unowned_database_identity_refuses_before_connection(tmp_path, dsn):
    with pytest.raises(BudgetDenied):
        DatabaseRestartBudget(dsn, tmp_path, IDENT, "a" * 40)


@pytest.mark.parametrize("value", ["", "{", "{}", "null", "[]"])
def test_corrupt_configuration_never_creates_a_new_budget(tmp_path, value):
    config = tmp_path / "config.json"
    config.write_text(value)
    with pytest.raises(BudgetDenied):
        DatabaseRestartBudget.from_config(config, tmp_path / "runtime", IDENT, "a" * 40)
    assert not (tmp_path / "runtime").exists()


def test_complete_release_identity_requires_config_and_never_falls_back(tmp_path):
    assert DatabaseRestartBudget.from_environment({}) is None
    with pytest.raises(BudgetDenied):
        DatabaseRestartBudget.from_environment({"TESTER_RESTART_BUDGET_DIR": str(tmp_path)})
    with pytest.raises(BudgetDenied):
        DatabaseRestartBudget.from_environment(
            dict(
                TESTER_RESTART_BUDGET_DIR=str(tmp_path),
                TESTER_INSTALLATION_ID=IDENT,
                TESTER_SOURCE_SHA="a" * 40,
            )
        )


def test_commit_failure_prevents_supervisor_spawn(tmp_path, monkeypatch):
    from src.api import db, supervisor

    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.__exit__.side_effect = psycopg.OperationalError("commit unavailable")
    monkeypatch.setattr(db, "connect", lambda _: connection)
    launches = []
    monkeypatch.setattr(supervisor.subprocess, "Popen", lambda *a, **k: launches.append(a))
    with pytest.raises(BudgetDenied):
        supervisor.launch_component("worker", budget(tmp_path))
    assert launches == []


def test_protected_config_binds_runtime_and_pins_numeric_loopback(tmp_path):
    config = tmp_path / "config.json"
    runtime = tmp_path / "runtime"
    config.write_text(json.dumps(dict(data_dir=str(runtime), database_url=DSN)))
    result = DatabaseRestartBudget.from_config(config, runtime, IDENT, "a" * 40)
    assert "hostaddr=127.0.0.1" in result.dsn
    with pytest.raises(BudgetDenied):
        DatabaseRestartBudget.from_config(config, tmp_path / "foreign", IDENT, "a" * 40)


def test_kill_switch_denies_without_database_io(tmp_path, monkeypatch):
    from src.api import db

    monkeypatch.setattr(db, "connect", lambda _: pytest.fail("Unexpected I/O"))
    (tmp_path / "restart.disabled").touch()
    with pytest.raises(BudgetDenied, match="disabled"):
        budget(tmp_path).admit("host")
