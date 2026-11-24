"""Issue #103: crash restarts are windowed; clean boots/operator starts are never counted.

PostgreSQL tests run against the disposable TEST_DATABASE_URL (platform workflow),
through a freshly created nonowner login that receives exactly the release grants
(EXECUTE on the two runtime functions, no table privilege), i.e. the real
restricted App-role path. Before migration 008 the lifetime counter denied the
fourth host start of an installation, so the clean reboot cycles below failed.
"""

import hashlib
import os
import secrets
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[2] / "src/api/migrations"
VALID_DSN = (
    "host=127.0.0.1 port=55822 dbname=tester user=tester_app password=temporary "
    "require_auth=scram-sha-256"
)
GRANTS = (
    "GRANT USAGE ON SCHEMA tester_platform TO {role};"
    "GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA tester_platform TO {role};"
    "GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA tester_platform TO {role};"
    "REVOKE INSERT,UPDATE,DELETE ON tester_platform.migrations FROM {role};"
    "REVOKE ALL ON tester_platform.restart_budgets FROM {role};"
    "GRANT EXECUTE ON FUNCTION tester_platform.reserve_restart(uuid,text,text), "
    "tester_platform.restart_counts(uuid,text) TO {role}"
)


def test_published_restart_migration_003_is_byte_identical():
    digest = hashlib.sha256((MIGRATIONS / "003_restart_budget.sql").read_bytes()).hexdigest()
    assert digest == "f2f8b1e966cbe588d696a9de901938fd3d03ed32d2ec6962392efb377ecdc765"
    assert sorted(p.name for p in MIGRATIONS.glob("008_*.sql")) == ["008_restart_window.sql"]


def _admin_dsn():
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Disposable TEST_DATABASE_URL required")
    return dsn


@contextmanager
def restricted_role(owner_dsn):
    """Nonowner login with exactly the provisioning grants of release_database."""
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    role, password = "tester_rw_" + secrets.token_hex(6), secrets.token_urlsafe(24)
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
        conn.execute(GRANTS.format(role=role))
    values = {k: v for k, v in conninfo_to_dict(owner_dsn).items() if k != "password"}
    try:
        yield make_conninfo(**{**values, "user": role, "password": password})
    finally:
        with psycopg.connect(owner_dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


@pytest.fixture
def authority(tmp_path):
    owner = _admin_dsn()
    import psycopg

    from src.api.db import migrate

    migrate(owner)
    migrate(owner)  # Idempotent: 003 checksum and 008 are recorded once.
    ident = str(uuid.uuid4())
    with psycopg.connect(owner) as conn:
        # Lifetime counters already exhausted, as on an installation hit by #103.
        conn.execute(
            "INSERT INTO tester_platform.restart_budgets"
            "(installation_id,source_sha,host_starts,api_starts,worker_starts) "
            "VALUES(%s,%s,3,6,6)",
            (ident, "a" * 40),
        )
    with restricted_role(owner) as app_dsn:

        def budget():
            from src.api.restart_budget import DatabaseRestartBudget

            value = DatabaseRestartBudget(VALID_DSN, tmp_path, ident, "a" * 40)
            value.dsn = app_dsn  # Identity validated above; connect as the restricted role.
            return value

        try:
            yield owner, app_dsn, ident, budget
        finally:
            with psycopg.connect(owner) as conn:
                conn.execute(
                    "DELETE FROM tester_platform.restart_budgets WHERE installation_id=%s",
                    (ident,),
                )


def _owner(owner, query, *args):
    import psycopg

    with psycopg.connect(owner) as conn:
        return conn.execute(query, args).fetchone()


@pytest.mark.integration
def test_actual_pg_clean_reboots_and_operator_starts_are_never_counted(authority):
    owner, _, ident, budget = authority
    # Install start + 20 reboot/operator stop-start cycles of fresh host processes.
    for _ in range(21):
        host = budget()
        host.admit("host")
        for role in ("api", "worker"):
            host.admit_initial(role)
        host.record_clean_stop()
    assert budget().counts() == {"host": 0, "api": 0, "worker": 0}
    status = _owner(owner, "SELECT tester_platform.restart_window_status(%s)", ident)[0]
    assert status["host_run_active"] is False and status["legacy_lifetime_starts"]["host"] == 3


@pytest.mark.integration
def test_actual_pg_crash_restarts_are_windowed_and_exhaustion_is_policy(authority):
    from src.api.restart_budget import BudgetDenied, BudgetUnavailable

    owner, _, ident, budget = authority
    first = budget()
    first.admit("host")
    first.admit("host")  # Same run retrying an ambiguous commit: idempotent.
    assert budget().counts()["host"] == 0
    for crash in range(1, 4):  # Previous host killed without a clean-stop record.
        budget().admit("host")
        assert budget().counts()["host"] == crash
    with pytest.raises(BudgetDenied) as denied:
        budget().admit("host")
    assert not isinstance(denied.value, BudgetUnavailable)
    assert budget().counts()["host"] == 3  # Denial rolls back; never exceeds the bound.
    # Crashes older than the window no longer refuse a start (boot or operator).
    _owner(
        owner,
        "UPDATE tester_platform.restart_budgets SET host_crashes="
        "ARRAY[now()-interval '3 hours',now()-interval '2 hours',now()-interval '61 minutes'] "
        "WHERE installation_id=%s RETURNING 1",
        ident,
    )
    recovered = budget()
    recovered.admit("host")
    assert budget().counts()["host"] == 1  # The unterminated run is counted exactly once.
    recovered.record_clean_stop()
    clean = budget()
    clean.admit("host")
    assert budget().counts()["host"] == 1


@pytest.mark.integration
def test_actual_pg_child_restarts_windowed_initial_launches_free(authority):
    from src.api.restart_budget import BudgetDenied

    _, _, _, budget = authority
    value = budget()
    for _ in range(10):
        value.admit_initial("worker")
    for _ in range(6):
        value.admit("worker")
    with pytest.raises(BudgetDenied):
        value.admit("worker")
    value.admit("api")
    assert value.counts() == {"host": 0, "api": 1, "worker": 6}


@pytest.mark.integration
def test_actual_pg_app_role_cannot_forge_reset_or_disable(authority):
    import psycopg

    from src.api.restart_budget import BudgetDenied

    owner, app_dsn, ident, budget = authority
    running = budget()
    running.admit("host")
    foreign = budget()
    foreign.host_token = secrets.token_hex(16)
    with pytest.raises(BudgetDenied):
        foreign.record_clean_stop()  # Only the exact host-run token ends the run.
    for statement in (
        "UPDATE tester_platform.restart_budgets SET host_crashes='{}', disabled=false",
        "DELETE FROM tester_platform.restart_budgets",
        "SELECT tester_platform.reset_restart_window('" + ident + "')",
        "SELECT tester_platform.restart_window_status('" + ident + "')",
        "SELECT tester_platform.restart_window_recent('{}')",
    ):
        with psycopg.connect(app_dsn) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)
    with psycopg.connect(app_dsn) as conn:
        for role in ("host", "host-stop:zz", "host-crash:" + "a" * 32, "api-reset", ""):
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute(
                    "SELECT tester_platform.reserve_restart(%s,%s,%s)", (ident, "a" * 40, role)
                )
            conn.rollback()
    # Owner kill switch denies every reservation kind; owner reset never clears it.
    _owner(
        owner,
        "UPDATE tester_platform.restart_budgets SET disabled=true WHERE installation_id=%s "
        "RETURNING 1",
        ident,
    )
    for action in (
        lambda: budget().admit("host"),
        lambda: budget().admit_initial("api"),
        lambda: budget().admit("worker"),
        lambda: budget().counts(),
    ):
        with pytest.raises(BudgetDenied):
            action()
    status = _owner(owner, "SELECT tester_platform.reset_restart_window(%s)", ident)[0]
    assert status["disabled"] is True and status["host_crashes"] == []
    with pytest.raises(BudgetDenied):
        budget().admit("host")


@pytest.mark.integration
def test_actual_pg_runtime_kill_switch_file_denies_without_sql(authority, tmp_path):
    from src.api.restart_budget import BudgetDenied

    (tmp_path / "restart.disabled").touch()
    with pytest.raises(BudgetDenied, match="disabled"):
        authority[3]().admit_initial("api")


@pytest.mark.integration
def test_actual_pg_existing_installation_upgrade_applies_008(tmp_path):
    """An installation migrated before 008 is repaired by the owner migrate path."""
    admin = _admin_dsn()
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from src.api.db import migrate
    from src.api.restart_budget import DatabaseRestartBudget

    name = "tester_upgrade_" + secrets.token_hex(5)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    target = make_conninfo(**{**conninfo_to_dict(admin), "dbname": name})
    ident = str(uuid.uuid4())
    try:
        with psycopg.connect(target) as conn:
            conn.execute("CREATE SCHEMA tester_platform")
            conn.execute(
                "CREATE TABLE tester_platform.migrations (version text PRIMARY KEY, "
                "checksum text NOT NULL, applied_at timestamptz DEFAULT now())"
            )
            for path in sorted(MIGRATIONS.glob("00[1-7]_*.sql")):
                content = path.read_text(encoding="utf-8")
                conn.execute(content)
                conn.execute(
                    "INSERT INTO tester_platform.migrations(version,checksum) VALUES(%s,%s)",
                    (path.name, hashlib.sha256(content.encode()).hexdigest()),
                )
            conn.execute(
                "INSERT INTO tester_platform.restart_budgets"
                "(installation_id,source_sha,host_starts) VALUES(%s,%s,3)",
                (ident, "b" * 40),
            )
        with restricted_role(target) as app_dsn:
            with psycopg.connect(app_dsn) as conn:
                with pytest.raises(psycopg.errors.RaiseException):
                    conn.execute(  # The pre-008 lifetime counter refuses a 4th start.
                        "SELECT tester_platform.reserve_restart(%s,%s,'host')", (ident, "b" * 40)
                    )
            migrate(target)
            budget = DatabaseRestartBudget(VALID_DSN, tmp_path, ident, "b" * 40)
            budget.dsn = app_dsn
            budget.admit("host")  # Existing EXECUTE grant now reaches the windowed policy.
            budget.record_clean_stop()
            assert budget.counts() == {"host": 0, "api": 0, "worker": 0}
            with psycopg.connect(app_dsn) as conn:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute("UPDATE tester_platform.restart_budgets SET host_crashes='{}'")
            with psycopg.connect(target) as conn:
                assert conn.execute(
                    "SELECT count(*) FROM tester_platform.migrations WHERE version=%s",
                    ("008_restart_window.sql",),
                ).fetchone() == (1,)
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
