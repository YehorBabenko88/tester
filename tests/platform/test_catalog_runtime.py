"""Opt-in catalog runtime contract; no installed database is contacted."""

import asyncio
import os
import subprocess
import sys
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from src.api.config import Settings
from src.api.main import create_app


class MemoryStore:
    def __init__(self):
        self.state = {
            category: dict(
                snapshot=None,
                attempt={"last_error": None, "at": None, "completeness": "UNKNOWN", "loaded": 0},
                pending=False,
                next_refresh_at=0,
                eligible_at=0,
                failures=0,
            )
            for category in ("linear", "inverse")
        }
        self.owner = True
        self.before_publish = None

    def claim(self, category):
        return self.owner

    def read(self, category):
        return deepcopy(self.state[category])

    def request(self, category):
        self.state[category]["pending"] = True

    def take_pending(self, category):
        pending = self.state[category]["pending"]
        self.state[category]["pending"] = False
        return pending

    def publish(self, category, snapshot, attempt, next_refresh_at, eligible_at, failures):
        if self.before_publish:
            self.before_publish()
        state = self.state[category]
        state.update(
            attempt=deepcopy(attempt),
            next_refresh_at=next_refresh_at,
            eligible_at=eligible_at,
            failures=failures,
        )
        if snapshot is not None:
            state["snapshot"] = deepcopy(snapshot)

    def close(self):
        pass


def test_persistent_budget_backoff_coalescing_and_committed_view():
    from src.api.persistent_market_catalog import PersistentCatalogService
    from tests.platform.test_market_catalog import Source

    now = [1000]
    source, store = Source(), MemoryStore()
    service = PersistentCatalogService(source, store, clock=lambda: now[0], jitter=lambda: 0)
    store.owner = False
    assert not service.refresh("linear")
    assert not source.calls
    store.owner = True
    store.before_publish = lambda: assert_unpublished(service)
    assert service.refresh("linear")
    store.before_publish = None
    assert store.state["linear"]["next_refresh_at"] == 1300
    assert service.query("linear")["total"] == 1200
    for _ in range(100):
        store.request("linear")
    assert not service.refresh("linear")
    assert len([call for call in source.calls if call[0] == "/tickers"]) == 1
    initial_calls = len(source.calls)
    now[0] = 1030
    source.fail = True
    assert not service.refresh("linear")
    state = store.state["linear"]
    assert state["next_refresh_at"] == state["eligible_at"] == 1630
    assert state["snapshot"]["last_success"] == 1000
    assert service.query("linear")["catalog"]["last_error"] == "source_failure"
    assert service.query("linear")["rows"][0]["metrics"]["price"]["state"] == "STALE"
    now[0] = 1100
    assert not service.refresh("linear")
    assert len(source.calls) == initial_calls + 1


def test_changed_exhausted_catalog_commits_even_when_ticker_attempt_failed(monkeypatch):
    from src.api.persistent_market_catalog import PersistentCatalogService

    store = MemoryStore()
    service = PersistentCatalogService(None, store, clock=lambda: 1000, jitter=lambda: 0)

    def partial_success(instance, category):
        instance._snapshots[category] = {"revision": "new-catalog", "rows": []}
        instance._attempts[category] = {
            "at": 1000,
            "last_error": "ticker_failure",
            "loaded": 0,
            "completeness": "EXHAUSTED",
        }
        return False

    monkeypatch.setattr("src.api.market_catalog.CatalogService.refresh", partial_success)
    assert not service.refresh("linear")
    assert store.state["linear"]["snapshot"]["revision"] == "new-catalog"
    assert store.state["linear"]["failures"] == 1


def assert_unpublished(service):
    assert service.query("linear")["revision"] is None


def test_persistent_300_second_schedule_and_manual_budget(monkeypatch):
    from src.api.persistent_market_catalog import PersistentCatalogService
    from tests.platform.test_market_catalog import Source

    async def scenario():
        now = [1000]
        source, store = Source(), MemoryStore()
        service = PersistentCatalogService(source, store, clock=lambda: now[0], jitter=lambda: 0)
        waits = []

        async def advance(awaitable, timeout):
            awaitable.close()
            waits.append(now[0])
            if len(waits) == 1:
                assert len([call for call in source.calls if call[0] == "/tickers"]) == 2
                for _ in range(100):
                    service.request_refresh("linear")
                now[0] = 1029
            elif len(waits) == 2:
                assert len([call for call in source.calls if call[0] == "/tickers"]) == 2
                now[0] = 1030
            elif len(waits) == 3:
                assert len([call for call in source.calls if call[0] == "/tickers"]) == 3
                now[0] = 1299
            elif len(waits) == 4:
                assert len([call for call in source.calls if call[0] == "/tickers"]) == 3
                now[0] = 1300
            else:
                assert (
                    len([call for call in source.calls if call[0] == "/tickers"]) == 4
                )  # inverse due
                service._cancel.set()

        service._wake = asyncio.Event()
        service._event_loop = asyncio.get_running_loop()
        monkeypatch.setattr("src.api.persistent_market_catalog.asyncio.wait_for", advance)
        await service._poll()
        assert store.state["linear"]["next_refresh_at"] == 1330
        assert store.state["inverse"]["next_refresh_at"] == 1600

    asyncio.run(scenario())


def test_global_watchlist_status_kind_and_refresh_route():
    from fastapi import FastAPI

    from src.api.market_catalog import CatalogService, create_market_router

    service = CatalogService(None, clock=lambda: 1000)
    service._snapshots["linear"] = {
        "revision": "r",
        "last_success": 1000,
        "source_time_ms": 1000000,
        "source_time_error": None,
        "rows": [
            {
                "symbol": symbol,
                "category": "linear",
                "status": status,
                "contract_type": "LinearPerpetual",
                "contract_kind": kind,
                "settle_coin": "USDT",
                "metrics": {},
            }
            for symbol, status, kind in [
                ("A", "Trading", "perpetual"),
                ("B", "Closed", "dated"),
                ("C", "Trading", "perpetual"),
            ]
        ],
    }
    result = service.query("linear", status="", watchlist=["linear:B"], contract_kind="dated")
    assert [row["symbol"] for row in result["rows"]] == ["B"]
    assert service.query("linear", watchlist=["inverse:A"])["total"] == 0
    with pytest.raises(ValueError):
        service.query("linear", watchlist=["bad"])
    app = FastAPI()
    app.include_router(create_market_router(service))
    with TestClient(app) as client:
        response = client.get(
            "/api/market-catalog/linear?status=&watchlist=linear:B&contract_kind=dated"
        )
        assert response.status_code == 200
        assert response.json()["data"]["total"] == 1
        assert client.post("/api/market-catalog/linear/refresh").status_code == 503


def test_catalog_disabled_by_default_and_requires_explicit_database():
    assert not Settings().market_catalog_enabled
    with TestClient(create_app(Settings()), base_url="http://127.0.0.1:8765") as client:
        assert client.get("/api/market-catalog/linear").status_code == 404
    with pytest.raises(ValueError):
        Settings(market_catalog_enabled=True)


def test_catalog_database_refuses_native_port():
    from src.api.catalog_store import validate_catalog_dsn

    for dsn in ["dbname=postgres", "host=127.0.0.1 port=5432 dbname=tester_catalog_test"]:
        with pytest.raises(ValueError):
            validate_catalog_dsn(dsn)
    validate_catalog_dsn("host=127.0.0.1 port=55471 dbname=tester_catalog_test")


def test_catalog_connection_boundary_pins_ambient_redirect_and_rejects_extras(monkeypatch):
    from psycopg.conninfo import conninfo_to_dict

    from src.api.catalog_store import CatalogStore, migrate_catalog, validate_catalog_dsn

    raw = "host=localhost port=55471 dbname=tester_catalog_test user=catalog"
    for suffix in [
        " hostaddr=192.0.2.77",
        " service=foreign",
        " options=redirect",
        " host=127.0.0.1,192.0.2.77",
        " port=05432",
    ]:
        with pytest.raises(ValueError):
            validate_catalog_dsn(raw + suffix)
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.77")
    captured = []

    class StoppedAtBoundary(Exception):
        pass

    def boundary(dsn):
        captured.append(conninfo_to_dict(dsn))
        raise StoppedAtBoundary

    monkeypatch.setattr("src.api.catalog_store.connect", boundary)
    store = CatalogStore(raw)
    for operation in [
        lambda: migrate_catalog(raw),
        lambda: store.claim("linear"),
        lambda: store.read("linear"),
        lambda: store.request("linear"),
    ]:
        with pytest.raises(StoppedAtBoundary):
            operation()
    assert len(captured) == 4
    assert all(values["hostaddr"] == values["host"] == "127.0.0.1" for values in captured)
    assert all(values["port"] == "55471" for values in captured)


def test_catalog_request_refuses_missing_migrated_category(monkeypatch):
    from src.api.catalog_store import CatalogStore

    class EmptyConnection:
        rowcount = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, *args):
            return self

    monkeypatch.setattr("src.api.catalog_store.connect", lambda dsn: EmptyConnection())
    store = CatalogStore("host=127.0.0.1 port=55471 dbname=tester_catalog_test")
    with pytest.raises(RuntimeError, match="migration"):
        store.request("linear")


@pytest.mark.integration
def test_postgres_ambient_redirect_is_pinned(stores, monkeypatch):
    a, _ = stores
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.77")
    assert a.read("linear")["category"] == "linear"


@pytest.fixture
def stores():
    from src.api.catalog_store import CatalogStore, migrate_catalog

    dsn = os.environ.get("TEST_CATALOG_DATABASE_URL")
    if not dsn:
        pytest.skip("explicit disposable TEST_CATALOG_DATABASE_URL required (non-5432)")
    migrate_catalog(dsn)
    migrate_catalog(dsn)
    a, b = CatalogStore(dsn), CatalogStore(dsn)
    try:
        yield a, b
    finally:
        a.close()
        b.close()


@pytest.mark.integration
def test_postgres_session_ownership_atomic_restart_and_pending_coalescing(stores):
    a, b = stores
    assert a.claim("linear")
    assert not b.claim("linear")
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os; from src.api.catalog_store import CatalogStore; "
            's=CatalogStore(os.environ["TEST_CATALOG_DATABASE_URL"]); '
            'print(s.claim("linear")); s.close()',
        ],
        capture_output=True,
        text=True,
        timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == "False"
    assert b.claim("inverse")
    before = a.read("linear")
    snapshot = {"revision": "test-revision", "rows": [], "last_success": 1000}
    attempt = {"last_error": None, "at": 1000, "loaded": 0, "completeness": "EXHAUSTED"}
    a.request("linear")
    a.request("linear")
    assert b.read("linear")["pending"]
    assert a.take_pending("linear")
    assert not a.take_pending("linear")
    a.publish("linear", snapshot, attempt, 1300, 1030, 0)
    assert b.read("linear")["snapshot"] == snapshot
    failure = {**attempt, "last_error": "rate_limited", "at": 1010}
    a.publish("linear", None, failure, 1610, 1610, 1)
    assert b.read("linear")["snapshot"] == snapshot
    assert b.read("linear")["attempt"] == failure
    with pytest.raises(RuntimeError):
        b.publish("linear", snapshot, attempt, 1300, 1030, 0)
    a.close()
    assert b.claim("linear")
    assert b.read("linear")["snapshot"] == snapshot
    assert before["category"] == "linear"


@pytest.mark.integration
def test_postgres_lost_owner_cannot_publish_and_successor_recovers(stores):
    import psycopg

    a, b = stores
    assert a.claim("linear")
    owner = a._owners["linear"]
    pid = owner.info.backend_pid
    from src.api.db import connect

    with connect(a.dsn) as conn:
        assert conn.execute("SELECT pg_terminate_backend(%s)", (pid,)).fetchone()[0]
    with pytest.raises((psycopg.Error, RuntimeError)):
        a.publish("linear", {"revision": "must-not-publish"}, {}, 0, 0, 0)
    assert b.claim("linear")
    assert (b.read("linear")["snapshot"] or {}).get("revision") != "must-not-publish"
