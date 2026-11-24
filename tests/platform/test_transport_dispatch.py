"""Scope-wide durable dispatch authority; no real network in these tests."""

from math import inf, nan
from unittest.mock import Mock
from uuid import uuid4

import pytest

from src.api.transport_policy import PostgresTransportPolicy, PublicPolicyUnavailable
from tests.platform.test_public_transport_policy import durable_policy as policy_fixture


@pytest.fixture
def durable_policy():
    yield from policy_fixture.__wrapped__()


def policy():
    return PostgresTransportPolicy(
        "host=127.0.0.1 port=55822 dbname=tester user=tester_app require_auth=scram-sha-256",
        uuid4(),
    )


@pytest.mark.parametrize(
    "row",
    [
        ("GRANTED", None, 0),
        ("GRANTED", str(uuid4()), 0.1),
        ("WAIT", None, 0),
        ("WAIT", None, inf),
        ("WAIT", None, nan),
        ("WAIT", None, 3601),
        ("PENDING", str(uuid4()), 0),
        ("HALTED", None, -1),
        ("CLEAR", None, 0),
        ("WAIT", None, True),
        ("PENDING", None),
        ("GRANTED", "invalid", 0),
    ],
)
def test_dispatch_adapter_rejects_malformed_authority(monkeypatch, row):
    value = policy()
    monkeypatch.setattr(value, "_call", lambda *args: row)
    with pytest.raises(PublicPolicyUnavailable):
        value.reserve_dispatch()


@pytest.mark.parametrize("cooldown", [None, True, -1, inf, nan, 3601, "1"])
def test_invalid_dispatch_cooldown_never_opens_database(monkeypatch, cooldown):
    value = policy()
    query = Mock()
    monkeypatch.setattr(value, "_call", query)
    with pytest.raises(PublicPolicyUnavailable):
        value.finish_dispatch(str(uuid4()), cooldown)
    query.assert_not_called()


def test_dispatch_adapter_commit_failure_is_fail_closed(monkeypatch):
    import src.api.transport_policy as module

    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(side_effect=OSError("controlled commit failure"))
    connection.execute.return_value.fetchone.return_value = ("GRANTED", uuid4(), 0)
    monkeypatch.setattr(module, "connect", lambda *a: connection)
    with pytest.raises(PublicPolicyUnavailable):
        policy().reserve_dispatch()


@pytest.mark.integration
def test_actual_pg_dispatch_scope_cooldown_and_stale_token(durable_policy):
    import psycopg

    from src.api.db import connect

    first, owner, scope = durable_policy
    fresh = PostgresTransportPolicy(first.dsn, scope)
    state, token, delay = first.reserve_dispatch()
    assert (state, delay) == ("GRANTED", 0)
    assert fresh.reserve_dispatch() == ("PENDING", None, 0)
    with pytest.raises(PublicPolicyUnavailable):
        fresh.finish_dispatch(str(uuid4()))
    assert fresh.reserve_dispatch() == ("PENDING", None, 0)
    first.finish_dispatch(token, 10)
    for instance in (first, fresh, PostgresTransportPolicy(first.dsn, scope)):
        state, token, delay = instance.reserve_dispatch()
        assert state == "WAIT" and token is None and 9 <= delay <= 10
    with pytest.raises(PublicPolicyUnavailable):
        first.reset()  # Runtime cannot clear its own cooldown.
    with connect(first.dsn) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE tester_platform.transport_policy_scopes SET dispatch_token=NULL")
    assert owner.reset()  # Explicit whole-scope owner review clears dispatch authority.
    state, token, _ = fresh.reserve_dispatch()
    assert state == "GRANTED"
    fresh.finish_dispatch(token)
    state, token, delay = first.reserve_dispatch()
    assert state == "WAIT" and token is None and 0 < delay <= 0.5


@pytest.mark.integration
def test_actual_pg_dead_dispatch_process_has_no_expiry(durable_policy):
    import os
    import subprocess
    import sys

    first, owner, scope = durable_policy
    script = """import os,sys
from src.api.transport_policy import PostgresTransportPolicy
p=PostgresTransportPolicy(os.environ['TEST_PUBLIC_POLICY_DSN'],sys.argv[1])
assert p.reserve_dispatch()[0]=='GRANTED'
"""
    subprocess.run(
        [sys.executable, "-c", script, str(scope)],
        check=True,
        capture_output=True,
        timeout=15,
        env=os.environ.copy(),
    )
    assert first.reserve_dispatch() == ("PENDING", None, 0)
    assert owner.reset()
    assert first.reserve_dispatch()[0] == "GRANTED"


@pytest.mark.integration
@pytest.mark.parametrize("timestamp", ["infinity", "-infinity", "2200-01-01"])
def test_actual_pg_dispatch_invalid_authority_timestamp_fails_closed(durable_policy, timestamp):
    from src.api.db import connect

    first, owner, scope = durable_policy
    with connect(owner.dsn) as conn:
        conn.execute(
            "UPDATE tester_platform.transport_policy_scopes SET dispatch_not_before=%s::timestamptz WHERE installation_id=%s",
            (timestamp, scope),
        )
    with pytest.raises(PublicPolicyUnavailable):
        first.reserve_dispatch()


@pytest.mark.parametrize(
    "state,token,delay",
    [
        ("GRANTED", str(uuid4()), 0),
        ("PENDING", None, 0),
        ("HALTED", None, 0),
        ("WAIT", None, 0.5),
    ],
)
def test_dispatch_adapter_accepts_only_well_formed_authority(monkeypatch, state, token, delay):
    value = policy()
    monkeypatch.setattr(value, "_call", lambda *args: (state, token, delay))
    assert value.reserve_dispatch() == (state, token, delay)


@pytest.mark.integration
def test_actual_pg_only_one_concurrent_dispatch_and_no_runtime_ddl(durable_policy):
    from concurrent.futures import ThreadPoolExecutor

    import psycopg

    from src.api.db import connect

    first, owner, scope = durable_policy

    def reserve(_):
        return PostgresTransportPolicy(first.dsn, scope).reserve_dispatch()

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(reserve, range(24)))
    assert sum(row[0] == "GRANTED" for row in outcomes) == 1
    assert sum(row[0] == "PENDING" for row in outcomes) == 23
    for statement in (
        "ALTER TABLE tester_platform.transport_policy_scopes ADD COLUMN forbidden boolean",
        "DELETE FROM tester_platform.transport_policy_scopes WHERE false",
        "DROP FUNCTION tester_platform.reserve_public_dispatch(uuid)",
    ):
        with connect(first.dsn) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as denied:
                conn.execute(statement)
            assert denied.value.sqlstate == "42501"
