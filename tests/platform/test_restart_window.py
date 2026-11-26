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
    # Same digest db.migrate records (universal newlines), independent of git autocrlf.
    content = (MIGRATIONS / "003_restart_budget.sql").read_text(encoding="utf-8")
    digest = hashlib.sha256(content.encode()).hexdigest()
    assert digest == "f2f8b1e966cbe588d696a9de901938fd3d03ed32d2ec6962392efb377ecdc765"
    assert sorted(p.name for p in MIGRATIONS.glob("008_*.sql")) == ["008_restart_window.sql"]


def _admin_dsn(capability="rolcreaterole"):
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Disposable TEST_DATABASE_URL required")
    import psycopg

    with psycopg.connect(dsn) as conn:
        allowed = conn.execute(
            f"SELECT rolsuper OR {capability} FROM pg_roles WHERE rolname=current_user"
        ).fetchone()[0]
    if not allowed:
        # The restricted role must be created fresh; the Linux platform gate's
        # disposable superuser runs these, a least-privilege installer DSN cannot.
        pytest.skip("Disposable TEST_DATABASE_URL cannot create the restricted test role")
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
def test_actual_pg_backward_clock_jump_preserves_crash_slots(authority):
    """No crash-budget bypass when wall time moves backwards by several hours.

    Owner injects persisted future timestamps, the equivalent of setting the
    system clock backwards after three crashes. The runtime App role must see
    those entries and deny a fourth crash restart. Old 008 dropped the entries.
    """
    import psycopg
    from src.api.restart_budget import BudgetDenied

    owner, app_dsn, ident, budget = authority
    previous = budget()
    previous.admit("host")
    _owner(
        owner,
        "UPDATE tester_platform.restart_budgets SET "
        "host_crashes=ARRAY[now()+interval '3 hours',now()+interval '4 hours',"
        "now()+interval '5 hours'] "
        "WHERE installation_id=%s RETURNING 1",
        ident,
    )
    assert budget().counts()["host"] == 3
    with pytest.raises(BudgetDenied):
        budget().admit("host")
    assert budget().counts()["host"] == 3
    with psycopg.connect(app_dsn) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT tester_platform.restart_window_recent(ARRAY[]::timestamptz[])")
    # Clean-stop still releases this active run without burning additional
    # crash slots, but does not reset the protected historical receipts.
    previous.record_clean_stop()
    assert budget().counts()["host"] == 3
    fresh = budget()
    fresh.admit("host")
    fresh.record_clean_stop()
    assert budget().counts()["host"] == 3


@pytest.mark.integration
def test_actual_pg_clean_stop_lost_ack_retry_is_idempotent_and_token_bound(authority):
    """SQL 011: an acknowledged/lost-ACK stop has the same durable outcome.

    Retry is permitted only for the most recent stop token, while no host run
    is active. Any new host run clears this acknowledgment receipt.
    """
    from src.api.restart_budget import BudgetDenied

    owner, _, ident, budget = authority
    first = budget()
    first.admit("host")
    assert first.host_token is not None
    token = first.host_token
    first.record_clean_stop()
    assert first.host_token is None
    # Simulate lost network acknowledgement: original host object still retains
    # the same token, and repeats the already committed owner-protected stop.
    first.host_token = token
    first.record_clean_stop()
    assert budget().counts()["host"] == 0
    state = _owner(owner, "SELECT tester_platform.restart_window_status(%s)", ident)[0]
    assert state["host_run_active"] is False
    with pytest.raises(BudgetDenied):
        second = budget()
        second.host_token = "f" * 32
        second.record_clean_stop()
    next_host = budget()
    next_host.admit("host")
    # A new host invalidates the old stop receipt; stale ACK cannot mark it clean.
    with pytest.raises(BudgetDenied):
        old = budget()
        old.host_token = token
        old.record_clean_stop()
    # An active run not recording clean stop is still charged next launch.
    after_crash = budget()
    after_crash.admit("host")
    assert budget().counts()["host"] == 1
    after_crash.record_clean_stop()
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
        for role in ("host:", "host-stop:zz", "host-crash:" + "a" * 32, "api-reset", "", "Host"):
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
def test_actual_pg_clean_stop_is_recorded_under_both_kill_switches(authority, tmp_path):
    """D3: a stop while a kill switch is set must not turn the next start into a crash.

    Before the fix both the runtime file and the owner flag refused 'host-stop', so
    the run stayed open and the first start after the switch was removed counted.
    """
    from src.api.restart_budget import BudgetDenied

    owner, _, ident, budget = authority
    running = budget()
    running.admit("host")
    (tmp_path / "restart.disabled").touch()
    running.record_clean_stop()
    with pytest.raises(BudgetDenied, match="disabled"):
        budget().admit("host")
    (tmp_path / "restart.disabled").unlink()
    second = budget()
    second.admit("host")
    assert budget().counts()["host"] == 0  # Clean start: not counted as a crash.
    _owner(
        owner,
        "UPDATE tester_platform.restart_budgets SET disabled=true WHERE installation_id=%s "
        "RETURNING 1",
        ident,
    )
    forged = budget()
    forged.host_token = secrets.token_hex(16)
    with pytest.raises(BudgetDenied):
        forged.record_clean_stop()  # Identity is still checked under the switch.
    second.record_clean_stop()
    for action in (
        lambda: budget().admit("host"),
        lambda: budget().admit_initial("worker"),
        lambda: budget().admit("api"),
    ):
        with pytest.raises(BudgetDenied):
            action()
    with pytest.raises(BudgetDenied):
        second.record_clean_stop()  # Token already consumed.
    _owner(
        owner,
        "UPDATE tester_platform.restart_budgets SET disabled=false WHERE installation_id=%s "
        "RETURNING 1",
        ident,
    )
    budget().admit("host")
    assert budget().counts()["host"] == 0


@pytest.mark.integration
def test_actual_pg_kill_switch_drain_is_recorded_as_clean_stop(authority, tmp_path, monkeypatch):
    """A supervisor drain caused by an owner kill switch ends the run cleanly.

    Before the fix the supervisor exited 1 and the service host treated that as an
    unexpected exit (no clean-stop record, SCM failure), so the first start after
    the owner removed the switch was counted as a host crash restart.
    """
    from src.api import release_service as rs
    from src.api.restart_budget import KILL_SWITCH_EXIT

    owner, _, ident, budget = authority

    def host():
        service = object.__new__(rs.ReleaseService)
        service.phases = []
        service.record_phase = service.phases.append
        service._restart_budget = budget()
        service._restart_budget.admit("host")
        return service

    def disabled(value):
        _owner(
            owner,
            "UPDATE tester_platform.restart_budgets SET disabled=%s WHERE installation_id=%s "
            "RETURNING 1",
            value,
            ident,
        )

    for switch in ("sql", "file"):
        running = host()
        if switch == "sql":
            disabled(True)
        else:
            (tmp_path / "restart.disabled").touch()
        assert running._child_exit_ends_run(tmp_path, KILL_SWITCH_EXIT) is True
        assert running.phases == ["drained-by-kill-switch"]
        status = _owner(owner, "SELECT tester_platform.restart_window_status(%s)", ident)[0]
        assert status["host_run_active"] is False
        disabled(False)
        (tmp_path / "restart.disabled").unlink(missing_ok=True)
    after = host()
    assert budget().counts()["host"] == 0  # Start after the switch is removed: clean.
    after._mark_clean_stop(tmp_path)
    # The exit status alone proves nothing: without a switch it is a crash.
    forged = host()
    assert forged._child_exit_ends_run(tmp_path, KILL_SWITCH_EXIT) is False
    host()
    assert budget().counts()["host"] == 1


@pytest.mark.integration
def test_actual_pg_runtime_kill_switch_file_denies_without_sql(authority, tmp_path):
    from src.api.restart_budget import BudgetDenied

    (tmp_path / "restart.disabled").touch()
    with pytest.raises(BudgetDenied, match="disabled"):
        authority[3]().admit_initial("api")


@contextmanager
def pre008_database(admin):
    """Fresh database migrated with exactly platform 001-007 (pre-008 installation)."""
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    name = "tester_upgrade_" + secrets.token_hex(5)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    target = make_conninfo(**{**conninfo_to_dict(admin), "dbname": name})
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
        yield target
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


def _legacy_reserve(app_dsn, ident, sha, role):
    """Exactly what pre-008 code (a1d38d0/d6059af restart_budget.py) sends."""
    import psycopg

    with psycopg.connect(app_dsn) as conn:
        conn.execute("SELECT tester_platform.reserve_restart(%s,%s,%s)", (ident, sha, role))


@pytest.mark.integration
def test_actual_pg_existing_installation_upgrade_applies_008(tmp_path):
    """An installation migrated before 008 is repaired by the owner migrate path."""
    admin = _admin_dsn("(rolcreaterole AND rolcreatedb)")
    import psycopg

    from src.api.db import migrate
    from src.api.restart_budget import DatabaseRestartBudget

    ident = str(uuid.uuid4())
    with pre008_database(admin) as target:
        with psycopg.connect(target) as conn:
            conn.execute(
                "INSERT INTO tester_platform.restart_budgets"
                "(installation_id,source_sha,host_starts) VALUES(%s,%s,3)",
                (ident, "b" * 40),
            )
        with restricted_role(target) as app_dsn:
            with pytest.raises(psycopg.errors.RaiseException):
                # The pre-008 lifetime counter refuses a 4th start.
                _legacy_reserve(app_dsn, ident, "b" * 40, "host")
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


@pytest.mark.integration
def test_actual_pg_legacy_untokenized_roles_are_windowed_after_008(tmp_path):
    """D2: pre-008 code restored on an upgraded schema starts, bounded by the window.

    Before the fix 008 denied the untokenized legacy role 'host' outright, so a
    pre-008 application could never start again on the upgraded database.
    """
    admin = _admin_dsn("(rolcreaterole AND rolcreatedb)")
    import psycopg

    from src.api.db import migrate
    from src.api.restart_budget import BudgetDenied, DatabaseRestartBudget

    ident, sha = str(uuid.uuid4()), "c" * 40
    with pre008_database(admin) as target:
        with psycopg.connect(target) as conn:
            conn.execute(
                "INSERT INTO tester_platform.restart_budgets(installation_id,source_sha) "
                "VALUES(%s,%s)",
                (ident, sha),
            )
        with restricted_role(target) as app_dsn:
            # Pre-008 behaviour: three lifetime host starts, then exhausted forever.
            for _ in range(3):
                _legacy_reserve(app_dsn, ident, sha, "host")
                _legacy_reserve(app_dsn, ident, sha, "api")  # Initial launch, pre-008 style.
            with pytest.raises(psycopg.errors.RaiseException):
                _legacy_reserve(app_dsn, ident, sha, "host")
            migrate(target)

            def counts():
                with psycopg.connect(app_dsn) as conn:
                    return conn.execute(
                        "SELECT tester_platform.restart_counts(%s,%s)", (ident, sha)
                    ).fetchone()[0]

            assert counts() == {"host": 0, "api": 0, "worker": 0}
            # Every legacy start is one windowed host crash restart: at most 3 per hour.
            for crash in range(1, 4):
                _legacy_reserve(app_dsn, ident, sha, "host")
                assert counts()["host"] == crash
            with pytest.raises(psycopg.errors.RaiseException):
                _legacy_reserve(app_dsn, ident, sha, "host")
            assert counts()["host"] == 3  # Denial rolled back; never exceeds the bound.
            # Legacy child launches (initial ones included) count in the child window.
            for _ in range(6):
                _legacy_reserve(app_dsn, ident, sha, "worker")
            with pytest.raises(psycopg.errors.RaiseException):
                _legacy_reserve(app_dsn, ident, sha, "worker")
            # Crashes that left the window admit legacy starts again.
            with psycopg.connect(target) as conn:
                conn.execute(
                    "UPDATE tester_platform.restart_budgets SET host_crashes="
                    "ARRAY[now()-interval '3 hours',now()-interval '2 hours',"
                    "now()-interval '61 minutes'] WHERE installation_id=%s",
                    (ident,),
                )
            _legacy_reserve(app_dsn, ident, sha, "host")
            assert counts()["host"] == 1
            # A legacy run can never be ended cleanly: the next tokenized start after
            # it is counted, and tokenized clean stop/start cycles then stay free.
            budget = DatabaseRestartBudget(VALID_DSN, tmp_path, ident, sha)
            budget.dsn = app_dsn
            budget.admit("host")
            assert budget.counts()["host"] == 2
            budget.record_clean_stop()
            for _ in range(5):
                clean = DatabaseRestartBudget(VALID_DSN, tmp_path, ident, sha)
                clean.dsn = app_dsn
                clean.admit("host")
                clean.admit_initial("api")
                clean.record_clean_stop()
            assert budget.counts()["host"] == 2
            # The owner kill switch still denies legacy starts.
            with psycopg.connect(target) as conn:
                conn.execute("UPDATE tester_platform.restart_budgets SET disabled=true")
            with pytest.raises(psycopg.errors.RaiseException):
                _legacy_reserve(app_dsn, ident, sha, "host")
            with pytest.raises(BudgetDenied):
                budget.counts()
