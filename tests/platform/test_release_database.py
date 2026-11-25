"""Release DB identity and migration matrix stay isolated from system PG."""

import os
from pathlib import Path

import pytest

from src.api.release_database import database_identity, runtime_configuration


def marker():
    return {
        "ports": {"postgres": 55822, "api": 55823},
        "source_sha": "a" * 40,
        "installation_id": "00000000-0000-0000-0000-000000000042",
    }


@pytest.mark.parametrize("port", [5432, 0, 65536, True, "55822"])
def test_release_database_refuses_unowned_or_malformed_port(port):
    value = marker()
    value["ports"]["postgres"] = port
    with pytest.raises(ValueError):
        database_identity(value)


def test_configuration_connects_existing_typed_catalog_history_adapter(tmp_path):
    result = runtime_configuration(tmp_path, marker(), "APP-DSN", "CATALOG-DSN")
    assert result["provider_factory"] == "src.integration.reference_prices:create_reader"
    assert result["handler_factory"] == "src.integration.reference_prices:create_handler"
    assert result["market_catalog_enabled"] is True
    assert result["market_catalog_database_url"] == "CATALOG-DSN"
    assert result["database_url"] == "APP-DSN"
    assert result["data_dir"] == str(tmp_path / "runtime")
    assert result["public_transport_policy_enabled"] is True
    assert result["public_transport_installation_id"] == marker()["installation_id"]


def test_projection_configuration_uses_only_owned_isolated_research_database(tmp_path):
    dsn = "host=127.0.0.1 port=55822 dbname=tester_research_release user=tester_research_app password=temporary"
    config = runtime_configuration(tmp_path, marker(), "APP-DSN", "CATALOG-DSN", research_dsn=dsn)
    assert config["research_projection_enabled"] is True
    assert config["research_database_url"] == dsn
    with pytest.raises(ValueError):
        runtime_configuration(
            tmp_path, marker(), "APP-DSN", "CATALOG-DSN", research_dsn=dsn.replace("55822", "5432")
        )
    with pytest.raises(ValueError):
        runtime_configuration(
            tmp_path, marker(), "APP-DSN", "CATALOG-DSN", research_dsn=dsn.replace("55822", "55823")
        )


def test_release_projection_migration_is_guarded_and_idempotent():
    from unittest.mock import MagicMock

    from src.api.history_lab_bootstrap import migrate_release_projections

    conn = MagicMock()
    conn.execute.return_value.fetchone.side_effect = [
        ("tester_storage.raw_records",),
        (None,),
        (None,),
    ]
    migrate_release_projections(conn)
    statements = [str(call.args[0]) for call in conn.execute.call_args_list]
    assert sum("CREATE TABLE tester_lab.projections" in sql for sql in statements) == 1
    assert any("INSERT INTO tester_lab.release_migrations" in sql for sql in statements)


def test_release_projection_refuses_foreign_existing_table_before_ddl():
    from unittest.mock import MagicMock

    from src.api.history_lab_bootstrap import migrate_release_projections

    conn = MagicMock()
    conn.execute.return_value.fetchone.side_effect = [
        ("tester_storage.raw_records",),
        (None,),
        ("tester_lab.projections",),
    ]
    with pytest.raises(RuntimeError, match="unregistered"):
        migrate_release_projections(conn)
    assert not any("CREATE " in str(call.args[0]) for call in conn.execute.call_args_list)


@pytest.mark.integration
def test_real_fresh_release_migration_roles_catalog_and_queue(tmp_path):
    import psycopg

    from src.api.catalog_store import CatalogStore, migrate_catalog
    from src.api.config import Settings
    from src.api.history_lab_bootstrap import migrate_release_projections
    from src.api.jobs import JobQueue
    from src.api.models import DownloadRequest
    from src.api.release_database import provision_release_databases
    from src.api.restart_budget import BudgetDenied, DatabaseRestartBudget
    from src.integration.catalog_market import CatalogHandler, catalog_lookup

    expected = os.environ.get("TESTER_RELEASE_DATABASE_TEST_ROOT")
    if not expected:
        pytest.skip("explicit fresh dedicated release DB cluster required")
    # Read the cluster identity before any DDL. A port alone cannot authorize it.
    from psycopg.conninfo import make_conninfo

    owner_password = os.environ.get(
        "TESTER_RELEASE_DATABASE_TEST_OWNER_PASSWORD", "disposable-test-only"
    )
    owner = make_conninfo(
        host="127.0.0.1",
        hostaddr="127.0.0.1",
        port=55822,
        dbname="postgres",
        user="tester_owner",
        password=owner_password,
        require_auth="scram-sha-256",
    )
    with psycopg.connect(owner) as conn:
        actual = conn.execute("SHOW data_directory").fetchone()[0]
    if Path(actual).resolve() != Path(expected).resolve() or not Path(expected).name.startswith(
        "release-database-pg"
    ):
        raise ValueError("Disposable release database scope mismatch")
    runtime, owners = provision_release_databases(marker(), owner_password)
    _assert_runtime_least_privileges(runtime, owners)
    for name, dsn in runtime.items():
        with psycopg.connect(dsn) as conn:
            assert (
                conn.execute(
                    "SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls "
                    "FROM pg_roles WHERE rolname=current_user"
                ).fetchone()
                == (False,) * 5
            )
            assert not conn.execute(
                "SELECT has_database_privilege(current_user,current_database(),'CREATE')"
            ).fetchone()[0]
    with psycopg.connect(owners["tester"]) as conn:
        migrate_release_projections(conn)
        migrate_release_projections(conn)
        conn.execute(
            "ALTER TABLE tester_lab.projections DROP CONSTRAINT projections_record_key_fkey"
        )
        with pytest.raises(RuntimeError, match="schema integrity"):
            migrate_release_projections(conn)
        conn.rollback()
    migrate_catalog(owners["tester_catalog"])
    config = Settings(
        **runtime_configuration(tmp_path, marker(), runtime["tester"], runtime["tester_catalog"])
    )
    handler = CatalogHandler(config)
    store = CatalogStore(config.market_catalog_dsn)
    try:
        assert store.claim("linear")
        observed = dict(
            category="linear",
            symbol="BTCUSDT",
            status="Trading",
            contract_kind="perpetual",
            contract_type="LinearPerpetual",
        )
        store.publish(
            "linear",
            {"revision": "synthetic-release-adapter", "rows": [observed], "last_success": 1000},
            {"at": 1000},
            1300,
            1030,
            0,
        )
        assert catalog_lookup(config)("linear", "BTCUSDT") == observed
        request = DownloadRequest(
            source="bybit",
            category="linear",
            symbol="BTCUSDT",
            kind="candles",
            interval="1",
            start_ms=1704067200000,
            end_ms=1704067260000,
        )
        handler.validator(request)
        queue = JobQueue(config.dsn)
        created = queue.create(request)
        lease = queue.claim()
        assert lease.id == created.id
        queue.checkpoint(lease.id, lease.lease_token, {"release_adapter": "verified"}, 0.5)
        queue.finish(lease.id, lease.lease_token)
        assert queue.get(created.id).checkpoint["release_adapter"] == "verified"
        with psycopg.connect(runtime["tester"]) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("UPDATE tester_platform.migrations SET checksum='forbidden'")
        reservation = DatabaseRestartBudget(
            runtime["tester"], tmp_path, marker()["installation_id"], marker()["source_sha"]
        )
        for _ in range(6):
            reservation.admit("worker")
        # A valid older local journal cannot roll back the SQL-owned authority.
        (tmp_path / "restart-budget.jsonl").write_text('{"old":"valid-prefix"}\n')
        with pytest.raises(BudgetDenied):
            reservation.admit("worker")
        assert reservation.counts()["worker"] == 6
        from concurrent.futures import ThreadPoolExecutor

        def reserve_host(_):
            # Each attempt is a distinct host run that never records a clean stop:
            # the first run is a clean start, every later one is a crash restart
            # bounded by the sliding window (migration 008, issue #103).
            host = DatabaseRestartBudget(
                runtime["tester"], tmp_path, marker()["installation_id"], marker()["source_sha"]
            )
            try:
                host.admit("host")
                return True
            except BudgetDenied:
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            assert sum(pool.map(reserve_host, range(8))) == 1 + 3
        assert (
            DatabaseRestartBudget(
                runtime["tester"], tmp_path, marker()["installation_id"], marker()["source_sha"]
            ).counts()["host"]
            == 3
        )
        with psycopg.connect(runtime["tester"]) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("UPDATE tester_platform.restart_budgets SET worker_starts=0")
        with psycopg.connect(runtime["tester"]) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("UPDATE tester_platform.restart_budgets SET host_crashes='{}'")
        with psycopg.connect(owners["tester"]) as conn:
            conn.execute("UPDATE tester_platform.restart_budgets SET disabled=true")
        with pytest.raises(BudgetDenied):
            reservation.admit("api")
    finally:
        store.close()


def test_release_runtime_grants_cannot_mint_research_authority_or_mutate_projections(monkeypatch):
    from unittest.mock import MagicMock

    import src.api.release_database as release

    conn = MagicMock()
    conn.__enter__.return_value = conn
    monkeypatch.setattr(release.psycopg, "connect", lambda *a, **k: conn)
    for name in (
        "migrate_platform",
        "migrate_storage",
        "migrate_release_projections",
        "migrate_catalog",
        "migrate_research",
    ):
        monkeypatch.setattr(release, name, lambda *a, **k: None)
    release.provision_release_databases(marker(), "disposable-unit-secret")
    statements = [
        call.args[0].as_string() if hasattr(call.args[0], "as_string") else call.args[0]
        for call in conn.execute.call_args_list
    ]
    # Interpret actual emitted grants/revocations, including broad schema grants.
    import re

    def privileges(table, schema, role):
        result = set()
        for statement in statements:
            match = re.fullmatch(
                r'(GRANT|REVOKE) (.+) ON (.+) (?:TO|FROM) "?' + role + r'"?', statement
            )
            if not match:
                continue
            verb, values, target = match.groups()
            target = target.replace('"', "")
            if target not in (f"{schema}.{table}", f"ALL TABLES IN SCHEMA {schema}"):
                continue
            values = set(values.split(","))
            if values == {"ALL"}:
                values = {
                    "SELECT",
                    "INSERT",
                    "UPDATE",
                    "DELETE",
                    "TRUNCATE",
                    "REFERENCES",
                    "TRIGGER",
                }
            result = result | values if verb == "GRANT" else result - values
        return result

    for table in ("credentials", "grants", "artifacts", "execution_inputs", "execution_grants"):
        assert privileges(table, "tester_research", "tester_research_app") == {"SELECT"}, table
    assert privileges("jobs", "tester_research", "tester_research_app") == {
        "SELECT",
        "INSERT",
        "UPDATE",
    }
    for table in ("acknowledgments", "audit"):
        assert privileges(table, "tester_research", "tester_research_app") == {"SELECT", "INSERT"}
    assert privileges("projections", "tester_lab", "tester_app") == {"SELECT", "INSERT"}
    # Issue #114: no DELETE/TRUNCATE on durable jobs/checkpoints; UPDATE only
    # where runtime writes (claims, ON CONFLICT DO UPDATE checkpoint upserts).
    for table, schema in (
        ("jobs", "tester_platform"),
        ("download_jobs", "tester_storage"),
        ("ingestion_checkpoints", "tester_storage"),
    ):
        assert privileges(table, schema, "tester_app") == {"SELECT", "INSERT", "UPDATE"}, table
    for table in ("raw_records", "record_sources", "pagination_evidence"):
        assert privileges(table, "tester_storage", "tester_app") == {"SELECT", "INSERT"}, table


def _assert_runtime_least_privileges(runtime, owners):
    """Actual PostgreSQL privilege checks; invoked only after fresh-cluster scope proof."""
    import hashlib
    from uuid import uuid4

    import psycopg

    from src.api.research_ledger import PersistentResearchLedger

    research = runtime["tester_research_release"]
    for statement in (
        "INSERT INTO tester_research.credentials(actor,token_hash,expires_at) "
        "VALUES('forbidden','" + "f" * 64 + "',clock_timestamp())",
        "UPDATE tester_research.credentials SET revoked=false WHERE false",
        "DELETE FROM tester_research.credentials WHERE false",
        "INSERT INTO tester_research.grants VALUES('forbidden','" + "f" * 64 + "','chart.overlay')",
        "UPDATE tester_research.grants SET action='chart.overlay' WHERE false",
        "DELETE FROM tester_research.grants WHERE false",
        "TRUNCATE tester_research.credentials CASCADE",
        "ALTER TABLE tester_research.credentials ADD COLUMN forbidden boolean",
    ):
        with psycopg.connect(research) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as denied:
                conn.execute(statement)
            assert denied.value.sqlstate == "42501"
    for statement in (
        "UPDATE tester_lab.projections SET record=record WHERE false",
        "DELETE FROM tester_lab.projections WHERE false",
        "TRUNCATE tester_lab.projections",
    ):
        with psycopg.connect(runtime["tester"]) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as denied:
                conn.execute(statement)
            assert denied.value.sqlstate == "42501"
    actor = "privilege-probe-" + uuid4().hex
    token = "disposable-privilege-probe-" + uuid4().hex
    with psycopg.connect(owners["tester_research_release"]) as conn:
        conn.execute(
            "INSERT INTO tester_research.credentials(actor,token_hash,expires_at) "
            "VALUES(%s,%s,clock_timestamp()+interval '60 seconds')",
            (actor, hashlib.sha256(token.encode()).hexdigest()),
        )
    ledger = PersistentResearchLedger(research)
    assert ledger.authenticate("Bearer " + token).actor == actor
    with ledger.connection() as conn:
        ledger._credential(conn, actor)  # definer guard locks without runtime UPDATE authority
    with psycopg.connect(owners["tester_research_release"]) as conn:
        conn.execute("UPDATE tester_research.credentials SET revoked=true WHERE actor=%s", (actor,))
    from src.api.research_control import Denied

    with pytest.raises(Denied):
        ledger.authenticate("Bearer " + token)
