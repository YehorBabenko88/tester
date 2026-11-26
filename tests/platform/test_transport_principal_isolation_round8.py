"""P0: one authenticated runtime PostgreSQL principal per installation.

All tests are disposable-only. Neither release production DSNs nor port 5432
are accepted. Tests must execute against a real PostgreSQL 18 fixture to
constitute acceptance; a skipped fixture is NOT a PASS.
"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def enrolled():
    from psycopg.conninfo import conninfo_to_dict

    from src.api.db import connect, migrate

    owner_dsn = os.environ.get("TEST_PUBLIC_POLICY_OWNER_DSN")
    app_dsn = os.environ.get("TEST_PUBLIC_POLICY_DSN")
    if not owner_dsn or not app_dsn:
        pytest.skip("Explicit disposable scoped PostgreSQL owner/App DSNs required")
    own, app = conninfo_to_dict(owner_dsn), conninfo_to_dict(app_dsn)
    if own.get("host") != "127.0.0.1" or app.get("host") != "127.0.0.1":
        raise ValueError("Only disposable loopback PostgreSQL accepted")
    if not 55820 <= int(own.get("port", "0")) <= 55827 or own.get("port") != app.get("port"):
        raise ValueError("Reserved disposable PG port 55820..55827 required")
    if own.get("dbname") != app.get("dbname"):
        raise ValueError("Owner and App must use same disposable database")
    # A module-scoped seed scope is intentionally absent: each test gets its
    # own isolated owner-enrolled App scope, preventing same-role interference.
    # The dedicated mandatory CI job provisions one disposable PG18 database
    # with independent owner/App login credentials and unprivileged functions.
    migrate(owner_dsn)
    with connect(owner_dsn) as conn:
        record = conn.execute(
            "SELECT checksum FROM tester_platform.migrations WHERE version=%s",
            ("012_transport_installation_principal.sql",),
        ).fetchone()
        assert record is not None, "Migration 012 absent from disposable PG fixture"
        role = conn.execute("SELECT current_user").fetchone()[0]
        assert role != app["user"], "Owner and runtime PostgreSQL principals must differ"
    scope = uuid.uuid4()
    with connect(owner_dsn) as conn:
        conn.execute(
            "INSERT INTO tester_platform.transport_policy_scopes"
            "(installation_id,runtime_role,owner_role) VALUES(%s,%s,current_user)",
            (scope, app["user"]),
        )
    try:
        yield owner_dsn, app_dsn, scope
    finally:
        with connect(owner_dsn) as conn:
            conn.execute(
                "DELETE FROM tester_platform.transport_policy_entries WHERE installation_id=%s",
                (scope,),
            )
            conn.execute(
                "DELETE FROM tester_platform.transport_policy_scopes WHERE installation_id=%s",
                (scope,),
            )


def test_pg_runtime_principal_unique_and_scope_forgery_denied(enrolled):
    import psycopg
    from psycopg.conninfo import conninfo_to_dict

    from src.api.db import connect
    from src.api.transport_policy import PostgresTransportPolicy, PublicPolicyUnavailable

    owner, app, scope = enrolled
    stranger = uuid.uuid4()
    login = conninfo_to_dict(app)["user"]
    with pytest.raises(psycopg.errors.UniqueViolation), connect(owner) as conn:
        conn.execute(
            "INSERT INTO tester_platform.transport_policy_scopes"
            "(installation_id,runtime_role,owner_role) VALUES(%s,%s,current_user)",
            (stranger, login),
        )
    foreign = PostgresTransportPolicy(app, stranger)
    with pytest.raises(PublicPolicyUnavailable):
        foreign.reserve_dispatch()
    own = PostgresTransportPolicy(app, scope)
    assert own.read("api.bybit.com/v5/market/kline") == "CLEAR"


def test_pg_runtime_cannot_delete_enrollment_or_reset_foreign_owner(enrolled):
    import psycopg

    from src.api.db import connect
    from src.api.transport_policy import PostgresTransportPolicy, PublicPolicyUnavailable

    owner, app, scope = enrolled
    policy = PostgresTransportPolicy(app, scope)
    with pytest.raises(PublicPolicyUnavailable):
        policy.reset("api.bybit.com/v5/market/kline")
    with pytest.raises(psycopg.errors.InsufficientPrivilege), connect(app) as conn:
        conn.execute("DELETE FROM tester_platform.transport_policy_scopes")
    with connect(owner) as conn:
        value = conn.execute(
            "SELECT count(*) FROM tester_platform.transport_policy_scopes WHERE installation_id=%s",
            (scope,),
        ).fetchone()[0]
        assert value == 1


def test_pg_concurrent_same_login_enrollment_serialized(enrolled):
    """First-time racing enrollments must yield exactly one winner."""
    import psycopg

    from src.api.db import connect

    owner, _, _ = enrolled
    login = os.environ.get("TEST_PUBLIC_POLICY_SECOND_APP_ROLE")
    assert login, "Second disposable runtime LOGIN not provisioned"
    identities = [uuid.uuid4(), uuid.uuid4()]
    gate = Barrier(2)

    def attempt(ident):
        gate.wait(timeout=15)
        try:
            with connect(owner) as conn:
                conn.execute(
                    "INSERT INTO tester_platform.transport_policy_scopes"
                    "(installation_id,runtime_role,owner_role) VALUES(%s,%s,current_user)",
                    (ident, login),
                )
        except psycopg.errors.UniqueViolation:
            return "DENIED"
        return "INSERTED"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(attempt, identities))
        assert sorted(outcomes) == ["DENIED", "INSERTED"]
        with connect(owner) as conn:
            assert conn.execute(
                "SELECT count(*) FROM tester_platform.transport_policy_scopes "
                "WHERE runtime_role=%s", (login,),
            ).fetchone()[0] == 1
    finally:
        with connect(owner) as conn:
            conn.execute(
                "DELETE FROM tester_platform.transport_policy_entries "
                "WHERE installation_id=ANY(%s)", (identities,),
            )
            conn.execute(
                "DELETE FROM tester_platform.transport_policy_scopes "
                "WHERE installation_id=ANY(%s)", (identities,),
            )



def test_pg_foreign_uuid_rejected_with_two_distinct_valid_logins(enrolled):
    """Two real enrolled scopes; App A cannot operate B's policy or leases."""
    from psycopg.conninfo import conninfo_to_dict

    from src.api.db import connect
    from src.api.transport_policy import PostgresTransportPolicy, PublicPolicyUnavailable

    owner, app, own_scope = enrolled
    login = conninfo_to_dict(app)["user"]
    # A separate unprivileged role must be provisioned by the disposable
    # PostgreSQL fixture. Omission is a failing required E2E gate, not SKIP.
    foreign_login = os.environ.get("TEST_PUBLIC_POLICY_SECOND_APP_ROLE")
    assert foreign_login, "Second disposable runtime LOGIN not provisioned"
    assert foreign_login != login
    foreign_scope = uuid.uuid4()
    with connect(owner) as conn:
        conn.execute(
            "INSERT INTO tester_platform.transport_policy_scopes"
            "(installation_id,runtime_role,owner_role) VALUES(%s,%s,current_user)",
            (foreign_scope, foreign_login),
        )
    try:
        other = PostgresTransportPolicy(app, foreign_scope)
        endpoint = "api.bybit.com/v5/market/kline"
        with pytest.raises(PublicPolicyUnavailable):
            other.read(endpoint)
        with pytest.raises(PublicPolicyUnavailable):
            other.reserve(endpoint)
        with pytest.raises(PublicPolicyUnavailable):
            other.reserve_dispatch()
        with pytest.raises(PublicPolicyUnavailable):
            other.finish(endpoint, uuid.uuid4(), "allow")
        with pytest.raises(PublicPolicyUnavailable):
            other.finish_dispatch(uuid.uuid4(), 0)
        with pytest.raises(PublicPolicyUnavailable):
            other.reset(endpoint)
        assert PostgresTransportPolicy(app, own_scope).read(endpoint) == "CLEAR"
    finally:
        with connect(owner) as conn:
            conn.execute(
                "DELETE FROM tester_platform.transport_policy_entries WHERE installation_id=%s",
                (foreign_scope,),
            )
            conn.execute(
                "DELETE FROM tester_platform.transport_policy_scopes WHERE installation_id=%s",
                (foreign_scope,),
            )


def test_pg_policy_halt_persists_and_dispatch_remains_fenced(enrolled):
    from src.api.db import connect
    from src.api.transport_policy import PostgresTransportPolicy

    owner, app, scope = enrolled
    policy = PostgresTransportPolicy(app, scope)
    with connect(owner) as conn:
        conn.execute(
            "UPDATE tester_platform.transport_policy_scopes SET globally_halted=true "
            "WHERE installation_id=%s", (scope,),
        )
    assert policy.read("api.bybit.com/v5/market/kline") == "HALTED"
    assert policy.reserve_dispatch()[0] == "HALTED"
    # Fixture cleanup must NOT reset globally_halted or silently unlock leased state.


def test_pg_duplicate_enrollment_rolls_back_without_mutating_existing_scope(enrolled):
    """Duplicate INSERT must fail without changing prior halt or leases."""
    import psycopg

    from src.api.db import connect

    owner, _, scope = enrolled
    stranger = uuid.uuid4()
    with connect(owner) as conn:
        original = conn.execute(
            "SELECT runtime_role, globally_halted, dispatch_token, dispatch_not_before "
            "FROM tester_platform.transport_policy_scopes WHERE installation_id=%s",
            (scope,),
        ).fetchone()
    assert original is not None
    with pytest.raises(psycopg.errors.UniqueViolation), connect(owner) as conn:
        conn.execute(
            "INSERT INTO tester_platform.transport_policy_scopes"
            "(installation_id,runtime_role,owner_role) VALUES(%s,%s,current_user)",
            (stranger, original[0]),
        )
    with connect(owner) as conn:
        assert conn.execute(
            "SELECT runtime_role, globally_halted, dispatch_token, dispatch_not_before "
            "FROM tester_platform.transport_policy_scopes WHERE installation_id=%s",
            (scope,),
        ).fetchone() == original
        assert conn.execute(
            "SELECT count(*) FROM tester_platform.transport_policy_scopes WHERE installation_id=%s",
            (stranger,),
        ).fetchone()[0] == 0
