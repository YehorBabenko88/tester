"""D3: the clean-stop record of the exact host run passes the runtime kill switch.

Before the fix ``_request`` refused every call while ``restart.disabled`` existed,
including ``record_clean_stop``; the run stayed open and the first start after the
switch was removed was counted as a crash restart.
"""

from unittest.mock import MagicMock

import pytest

pytest.importorskip("psycopg")

from src.api.restart_budget import BudgetDenied, DatabaseRestartBudget  # noqa: E402

IDENT = "00000000-0000-0000-0000-000000000042"
DSN = (
    "host=127.0.0.1 port=55822 dbname=tester user=tester_app password=temporary "
    "require_auth=scram-sha-256"
)


def connected(monkeypatch):
    from src.api import db

    calls = []
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.side_effect = lambda query, args=None: calls.append(args) or MagicMock()
    monkeypatch.setattr(db, "connect", lambda _: connection)
    return calls


def test_clean_stop_is_recorded_while_kill_switch_file_exists(tmp_path, monkeypatch):
    calls = connected(monkeypatch)
    budget = DatabaseRestartBudget(DSN, tmp_path, IDENT, "a" * 40)
    budget.admit("host")
    token = budget.host_token
    (tmp_path / "restart.disabled").touch()
    budget.record_clean_stop()
    assert calls[-1] == (IDENT, "a" * 40, "host-stop:" + token)
    assert budget.host_token is None


def test_kill_switch_file_still_denies_every_start_without_io(tmp_path, monkeypatch):
    from src.api import db

    monkeypatch.setattr(db, "connect", lambda _: pytest.fail("Unexpected I/O"))
    (tmp_path / "restart.disabled").touch()
    budget = DatabaseRestartBudget(DSN, tmp_path, IDENT, "a" * 40)
    for action in (
        lambda: budget.admit("host"),
        lambda: budget.admit("api"),
        lambda: budget.admit("worker"),
        lambda: budget.admit_initial("api"),
        lambda: budget.admit_initial("worker"),
        budget.counts,
    ):
        with pytest.raises(BudgetDenied, match="disabled"):
            action()
    # Without an admitted run there is nothing to stop; no SQL either.
    with pytest.raises(BudgetDenied, match="No admitted host run"):
        budget.record_clean_stop()
