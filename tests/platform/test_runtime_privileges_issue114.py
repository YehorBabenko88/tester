"""Real restricted-role PostgreSQL acceptance for #114 and #104 (CI only).

Provisions the fresh release databases with the production code path
(provision_release_databases), then runs every DATA/queue flow as the
non-superuser runtime role tester_app. Requires a DISPOSABLE cluster:
TESTER_PRIVILEGE_TEST_ADMIN_DSN (superuser, SCRAM host auth, non-5432 port).
No network, no real user data.
"""

import base64
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import urlencode
from uuid import uuid4

import pytest

ADMIN = os.environ.get("TESTER_PRIVILEGE_TEST_ADMIN_DSN")
pytestmark = pytest.mark.skipif(not ADMIN, reason="disposable privilege cluster required")
OWNER_PASSWORD = "disposable-privilege-owner-" + uuid4().hex
DURABLE = (
    "tester_storage.ingestion_checkpoints",
    "tester_storage.download_jobs",
    "tester_platform.jobs",
)
BASE_MS = 1700000000000
OBSERVED = datetime(2026, 11, 13, 20, tzinfo=UTC)


class Replay:
    """Original-HTTP replay of one recent-trade page (no network)."""

    def __init__(self, rows, padding=0):
        self.result = {"category": "linear", "list": rows}
        if padding:
            self.result["padding"] = "x" * padding

    def call(self, path, params):
        assert path == "/recent-trade"
        url = "https://api.bybit.com/v5/market/recent-trade?" + urlencode(params)
        body = json.dumps({"retCode": 0, "result": self.result}).encode()
        self.last_response = {
            "url": url,
            "status_code": 200,
            "hash_scope": "original_http_body",
            "sha256": hashlib.sha256(body).hexdigest(),
            "bytes_base64": base64.b64encode(body).decode(),
        }
        return self.result, url


def trade(symbol, index, side="Buy"):
    return {
        "symbol": symbol,
        "execId": f"{symbol}-priv-{index}",
        "time": str(BASE_MS + index),
        "price": "100.5",
        "size": "0.25",
        "side": side,
    }


@pytest.fixture(scope="module")
def release():
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict

    from src.api.release_database import provision_release_databases

    port = int(conninfo_to_dict(ADMIN)["port"])
    assert port != 5432
    with psycopg.connect(ADMIN, autocommit=True) as admin:
        assert admin.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user").fetchone()[
            0
        ]
        for name in ("tester", "tester_catalog", "tester_research_release"):
            if admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone():
                raise RuntimeError("refusing non-disposable cluster: " + name)
        admin.execute(
            sql.SQL("CREATE ROLE tester_owner LOGIN CREATEDB CREATEROLE PASSWORD {}").format(
                sql.Literal(OWNER_PASSWORD)
            )
        )
    marker = {
        "installation_id": str(uuid4()),
        "source_sha": "a" * 40,
        "ports": {"postgres": port, "api": 18099},
    }
    runtime, owners = provision_release_databases(marker, OWNER_PASSWORD)
    return SimpleNamespace(app=runtime["tester"], owner=owners["tester"], marker=marker)


def assert_no_delete(dsn):
    import psycopg

    with psycopg.connect(dsn) as conn:
        role = conn.execute(
            "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        assert role == (False, False)
        for table in DURABLE:
            granted = conn.execute(
                "SELECT has_table_privilege(current_user,%s,'DELETE'),"
                "has_table_privilege(current_user,%s,'TRUNCATE')",
                (table, table),
            ).fetchone()
            assert granted == (False, False), table
    for table in DURABLE:
        for statement in (f"DELETE FROM {table} WHERE false", f"TRUNCATE {table}"):
            with psycopg.connect(dsn) as conn:
                with pytest.raises(psycopg.errors.InsufficientPrivilege) as denied:
                    conn.execute(statement)
                assert denied.value.sqlstate == "42501", statement
    with psycopg.connect(dsn) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE tester_storage.raw_records SET payload=payload WHERE false")


def assert_queue_flow(dsn):
    from src.api.jobs import JobQueue
    from src.api.models import DownloadRequest

    queue = JobQueue(dsn)
    created = queue.create(
        DownloadRequest(
            source="bybit",
            category="linear",
            symbol="BTCUSDT",
            kind="candles",
            interval="1",
            start_ms=1704067200000,
            end_ms=1704067260000,
        )
    )
    lease = queue.claim()
    assert lease.id == created.id
    queue.checkpoint(lease.id, lease.lease_token, {"issue114": "restricted"}, 0.5)
    queue.finish(lease.id, lease.lease_token)
    assert queue.get(created.id).checkpoint["issue114"] == "restricted"


def test_fresh_install_runtime_cannot_delete_durable_rows(release):
    import psycopg

    assert_no_delete(release.app)
    with psycopg.connect(release.owner) as conn:
        assert not conn.execute(
            "SELECT count(*) FROM pg_default_acl WHERE defaclacl::text LIKE '%tester_app%'"
        ).fetchone()[0]


def test_restricted_runtime_queue_ingest_checkpoint_replay_and_recovery(release):
    from src.ingest.public_auxiliary import fetch_and_ingest
    from src.storage.store import Store

    assert_queue_flow(release.app)
    store = Store(release.app)
    captured = {}

    class Checkpointing:
        def ingest(self, records, evidence, job_key, run_id=None):
            captured.update(evidence=evidence, job_key=job_key)
            return store.ingest(records, evidence, job_key, {"page": 1}, run_id=run_id)

    rows = [trade("XRPUSDT", 0)]
    kwargs = dict(
        kind="trades",
        category="linear",
        symbol="XRPUSDT",
        run_id="issue114-run",
        job_key="issue114-job",
        observed_at=OBSERVED,
    )
    first = fetch_and_ingest(Checkpointing(), Replay(rows), **kwargs)
    replay = fetch_and_ingest(Checkpointing(), Replay(rows), **kwargs)
    assert first["count"] == replay["count"] == 1
    assert store.checkpoint("issue114-job")["page"] == 1
    store.mark_range(captured["evidence"], "corrupt", "issue114 recovery probe")
    jobs = store.recovery_jobs("bybit", "linear", "XRPUSDT", "trades", "")
    assert jobs

    def fetcher(job):
        raise RuntimeError("simulated crash before publication")

    results = store.recover(fetcher, "bybit", "linear", "XRPUSDT", "trades", "")
    assert results and all(
        r["state"] == "retry" and r["error_code"] == "RuntimeError" for r in results
    )


def test_restricted_runtime_data_execution_probe_and_bounds(release):
    from src.api.capability_data_bridge import _PROBE_BUDGET, observed_execution_evidence
    from src.ingest.execution_contract import ReadBudgetExceeded, read_execution_events
    from src.ingest.public_auxiliary import fetch_and_ingest
    from src.storage.store import Store

    store = Store(release.app)

    def query(symbol, count):
        return SimpleNamespace(
            category="linear",
            symbol=symbol,
            start_ms=BASE_MS,
            end_ms=BASE_MS + count,
            information_cutoff_ms=None,
        )

    def probe(symbol, count):
        started = time.monotonic()
        result = observed_execution_evidence(SimpleNamespace(store=store), query(symbol, count))
        assert time.monotonic() - started < _PROBE_BUDGET["timeout_ms"] / 1000 + 8
        return result

    # Positive: ported DATA path writes and reads back as tester_app.
    fetch_and_ingest(
        store,
        Replay([trade("ADAUSDT", 0), trade("ADAUSDT", 1, "Unknown")]),
        kind="trades",
        category="linear",
        symbol="ADAUSDT",
        run_id="issue104-ok",
        job_key="issue104-ok",
        observed_at=OBSERVED,
    )
    events = read_execution_events(
        store, category="linear", symbol="ADAUSDT", start_ms=BASE_MS, end_ms=BASE_MS + 2, limit=10
    )
    assert [e["taker_side"] for e in events] == ["Buy", "Unknown"]
    assert probe("ADAUSDT", 2)["observed_events"] == 2

    # One record with many source observations (fan-out).
    for index in range(_PROBE_BUDGET["max_observations_per_record"] + 1):
        fetch_and_ingest(
            store,
            Replay([trade("DOTUSDT", 0)]),
            kind="trades",
            category="linear",
            symbol="DOTUSDT",
            run_id=f"issue104-fanout-{index}",
            job_key=f"issue104-fanout-{index}",
            observed_at=OBSERVED,
        )
    result = probe("DOTUSDT", 1)
    assert (result["reason"], result["budget"]) == (
        "data72_read_budget_exceeded",
        "max_observations_per_record",
    )

    # 100 records spread over three multi-MB original HTTP pages.
    for page in range(3):
        rows = [trade("LINKUSDT", i) for i in range(page * 34, min(100, page * 34 + 34))]
        fetch_and_ingest(
            store,
            Replay(rows, padding=2_900_000),
            kind="trades",
            category="linear",
            symbol="LINKUSDT",
            run_id=f"issue104-large-{page}",
            job_key=f"issue104-large-{page}",
            observed_at=OBSERVED,
        )
    result = probe("LINKUSDT", 100)
    assert (result["reason"], result["budget"]) == (
        "data72_read_budget_exceeded",
        "max_evidence_bytes",
    )
    assert result["admitted_to_f"] is False and "observed_events" not in result
    started = time.monotonic()
    with pytest.raises(ReadBudgetExceeded) as caught:
        read_execution_events(
            store,
            category="linear",
            symbol="LINKUSDT",
            start_ms=BASE_MS,
            end_ms=BASE_MS + 100,
            limit=100,
        )
    assert caught.value.budget == "max_evidence_bytes"
    assert time.monotonic() - started < 15


def test_existing_database_upgrade_migration_revokes_broad_grants(release):
    import psycopg

    from src.api.db import migrate as migrate_platform

    # Recreate the grants of the former provisioning code on this database.
    with psycopg.connect(release.owner) as conn:
        conn.execute(
            "GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA "
            "tester_platform,tester_storage TO tester_app"
        )
        conn.execute(
            "REVOKE ALL ON tester_platform.restart_budgets,tester_platform.transport_policy_scopes,"
            "tester_platform.transport_policy_entries FROM tester_app"
        )
        conn.execute(
            "REVOKE INSERT,UPDATE,DELETE ON tester_platform.migrations,"
            "tester_storage.schema_migrations FROM tester_app"
        )
        conn.execute(
            "DELETE FROM tester_platform.migrations WHERE version='009_runtime_least_privilege.sql'"
        )
    with psycopg.connect(release.app) as conn:
        assert conn.execute(
            "SELECT has_table_privilege(current_user,'tester_platform.jobs','DELETE')"
        ).fetchone()[0]
    migrate_platform(release.owner)
    assert_no_delete(release.app)
    assert_queue_flow(release.app)
    migrate_platform(release.owner)  # idempotent: recorded once, no further change
