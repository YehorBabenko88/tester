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


@pytest.mark.integration
@pytest.mark.parametrize("status,cooldown", [(200, 0.5), (429, 1)])
def test_actual_pg_committed_dispatch_with_lost_ack_keeps_endpoint_and_sql_spacing(
    durable_policy, status, cooldown
):
    import time

    from src.ingest.transport import Transport
    from tests.platform.test_public_transport_policy import URL, response

    first, owner, scope = durable_policy
    original_finish = first.finish_dispatch
    committed_at, completions = [], []

    def commit_then_lose_ack(token, delay=0):
        completions.append(token)
        original_finish(token, delay)  # Real PostgreSQL COMMIT completes successfully.
        committed_at.append(time.monotonic())
        raise OSError("controlled lost completion acknowledgment")

    first.finish_dispatch = commit_then_lose_ack
    received = response(status)
    if status == 429:
        received.headers["Retry-After"] = "1"
    session = Mock()
    session.request.return_value = received
    with pytest.raises(OSError, match="lost completion acknowledgment"):
        Transport(session=session, policy_store=first, attempts=1).get(URL)
    assert len(completions) == 1  # An uncertain completion is never retried.
    fresh = PostgresTransportPolicy(first.dsn, scope)
    assert fresh.read(URL.removeprefix("https://")) == "PENDING"
    state, token, remaining = fresh.reserve_dispatch()
    assert state == "WAIT" and token is None and 0 < remaining <= cooldown
    same_endpoint = Mock()
    with pytest.raises(RuntimeError):
        Transport(session=same_endpoint, policy_store=fresh, sleep=Mock()).get(URL)
    same_endpoint.request.assert_not_called()
    other_endpoint = Mock()
    sent = []

    def observed_send(*args, **kwargs):
        sent.append(time.monotonic())
        return response()

    other_endpoint.request.side_effect = observed_send
    Transport(session=other_endpoint, policy_store=fresh, attempts=1).get(
        "https://api.bybit.com/v5/market/kline"
    )
    assert len(sent) == 1 and sent[0] - committed_at[0] >= cooldown - 0.02
    assert len(completions) == 1
    assert fresh.read(URL.removeprefix("https://")) == "PENDING"


@pytest.mark.integration
def test_actual_pg_existing_004_005_upgrade_preserves_jobs_checkpoints_and_policy(
    durable_policy, tmp_path, monkeypatch
):
    import hashlib
    import shutil
    from pathlib import Path

    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from src.api import db

    app, owner, _ = durable_policy
    role = conninfo_to_dict(app.dsn)["user"]
    database = "tester_dispatch_upgrade_" + uuid4().hex
    owner_upgrade = make_conninfo(owner.dsn, dbname=database)
    app_upgrade = make_conninfo(app.dsn, dbname=database)
    scope, job = uuid4(), uuid4()
    directory = Path(db.__file__).with_name("migrations")
    staged = tmp_path / "old-platform" / "migrations"
    staged.mkdir(parents=True)
    for path in sorted(directory.glob("*.sql")):
        if path.name[:3] <= "005":
            shutil.copyfile(path, staged / path.name)
    with psycopg.connect(owner.dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    try:
        with monkeypatch.context() as patch:
            patch.setattr(db, "__file__", str(staged.parent / "db.py"))
            db.migrate(owner_upgrade)
        with psycopg.connect(owner_upgrade) as conn:
            conn.execute(
                "INSERT INTO tester_platform.transport_policy_scopes(installation_id,runtime_role,owner_role) VALUES(%s,%s,current_user)",
                (scope, role),
            )
            conn.execute(
                "INSERT INTO tester_platform.transport_policy_entries VALUES(%s,'api.bybit.com/v5/market/time',%s,'HALTED','tls_failure',clock_timestamp()),(%s,'api.bybit.com/v5/market/kline',%s,'PENDING',NULL,clock_timestamp())",
                (scope, uuid4(), scope, uuid4()),
            )
            conn.execute(
                "INSERT INTO tester_platform.jobs(id,request,checkpoint,state) VALUES(%s,'{}',%s,'paused')",
                (job, '{"retained_page":42}'),
            )
            conn.execute(
                sql.SQL("GRANT USAGE ON SCHEMA tester_platform TO {}").format(sql.Identifier(role))
            )
            conn.execute(
                sql.SQL(
                    "GRANT EXECUTE ON FUNCTION tester_platform.read_public_transport(uuid,text),tester_platform.reserve_public_transport(uuid,text),tester_platform.finish_public_transport(uuid,text,uuid,text) TO {}"
                ).format(sql.Identifier(role))
            )
            before_checksums = conn.execute(
                "SELECT version,checksum FROM tester_platform.migrations ORDER BY version"
            ).fetchall()
            before_entries = conn.execute(
                "SELECT * FROM tester_platform.transport_policy_entries ORDER BY endpoint_key"
            ).fetchall()
            before_scopes = conn.execute(
                "SELECT installation_id,runtime_role,owner_role,schema_version,globally_halted FROM tester_platform.transport_policy_scopes"
            ).fetchall()
            before_jobs = conn.execute("SELECT * FROM tester_platform.jobs").fetchall()
        db.migrate(owner_upgrade)  # Existing owner path, no fresh provisioning grants.
        db.migrate(owner_upgrade)  # Idempotent normal migration retry.
        with psycopg.connect(owner_upgrade) as conn:
            assert conn.execute("SELECT * FROM tester_platform.jobs").fetchall() == before_jobs
            assert (
                conn.execute(
                    "SELECT * FROM tester_platform.transport_policy_entries ORDER BY endpoint_key"
                ).fetchall()
                == before_entries
            )
            assert (
                conn.execute(
                    "SELECT installation_id,runtime_role,owner_role,schema_version,globally_halted FROM tester_platform.transport_policy_scopes"
                ).fetchall()
                == before_scopes
            )
            assert (
                conn.execute(
                    "SELECT version,checksum FROM tester_platform.migrations WHERE version<'006' ORDER BY version"
                ).fetchall()
                == before_checksums
            )
            for version, digest in before_checksums:
                assert (
                    hashlib.sha256(
                        (directory / version).read_text(encoding="utf-8").encode()
                    ).hexdigest()
                    == digest
                )
        upgraded = PostgresTransportPolicy(app_upgrade, scope)
        assert upgraded.read("api.bybit.com/v5/market/time") == "HALTED"
        assert upgraded.read("api.bybit.com/v5/market/kline") == "PENDING"
        state, token, _ = upgraded.reserve_dispatch()
        assert state == "GRANTED"
        upgraded.finish_dispatch(token)
        with pytest.raises(PublicPolicyUnavailable):
            upgraded.reset()
        with psycopg.connect(app_upgrade) as conn:
            for table in ("transport_policy_scopes", "transport_policy_entries"):
                assert not conn.execute(
                    "SELECT has_table_privilege(current_user,%s,'UPDATE')",
                    ("tester_platform." + table,),
                ).fetchone()[0]
                assert not conn.execute(
                    "SELECT has_table_privilege(current_user,%s,'INSERT')",
                    ("tester_platform." + table,),
                ).fetchone()[0]
    finally:
        assert database.startswith("tester_dispatch_upgrade_")
        with psycopg.connect(owner.dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))


@pytest.mark.integration
@pytest.mark.parametrize(
    "bad_state", ["foreign_owner", "owner_as_runtime", "missing_runtime", "function_owner"]
)
def test_actual_pg_upgrade_grant_refuses_untrusted_enrollment(durable_policy, bad_state):
    from pathlib import Path

    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict

    from src.api import db

    app, owner, scope = durable_policy
    migration = Path(db.__file__).with_name("migrations") / "007_public_dispatch_grants.sql"
    with psycopg.connect(owner.dsn) as conn:
        if bad_state == "foreign_owner":
            conn.execute(
                "UPDATE tester_platform.transport_policy_scopes SET owner_role='not_the_owner' WHERE installation_id=%s",
                (scope,),
            )
        elif bad_state == "owner_as_runtime":
            conn.execute(
                "UPDATE tester_platform.transport_policy_scopes SET runtime_role=current_user WHERE installation_id=%s",
                (scope,),
            )
        elif bad_state == "missing_runtime":
            conn.execute(
                "UPDATE tester_platform.transport_policy_scopes SET runtime_role=%s WHERE installation_id=%s",
                ("missing_" + uuid4().hex, scope),
            )
        else:
            conn.execute(
                sql.SQL(
                    "ALTER FUNCTION tester_platform.reserve_public_dispatch(uuid) OWNER TO {}"
                ).format(sql.Identifier(conninfo_to_dict(app.dsn)["user"]))
            )
        with pytest.raises(psycopg.errors.RaiseException, match="invalid"):
            conn.execute(migration.read_text(encoding="utf-8"))
        conn.rollback()  # Restore the deliberately altered protected fixture.
