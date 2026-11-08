import asyncio
import json
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.market_catalog import (
    AcquisitionError,
    BybitCatalogSource,
    CatalogService,
    SnapshotConflict,
    create_market_router,
)


class Source:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.cycle = False

    def call(self, endpoint, params):
        self.calls.append((endpoint, dict(params)))
        if self.fail:
            raise OSError("offline")
        if endpoint == "/tickers":
            return {
                "category": params["category"],
                "_source_time_ms": 1000000,
                "list": [
                    {"symbol": "S1199", "lastPrice": "999999", "volume24h": "123"},
                    {"symbol": "S0000", "lastPrice": "1"},
                ],
            }, "https://api.bybit.com/v5/market/tickers"
        if params["status"] != "Trading":
            return {
                "category": params["category"],
                "list": [],
                "nextPageCursor": "",
            }, "https://api.bybit.com/v5/market/instruments-info"
        start = 1000 if params.get("cursor") else 0
        rows = [
            {
                "symbol": f"S{i:04}",
                "status": "Trading",
                "contractType": "LinearPerpetual",
                "settleCoin": "USDT",
            }
            for i in range(start, min(start + 1000, 1200))
        ]
        return {
            "category": params["category"],
            "list": rows,
            "nextPageCursor": "next" if start == 0 or self.cycle else "",
        }, "https://api.bybit.com/v5/market/instruments-info"


def test_complete_universe_global_sort_and_unavailable_metrics():
    source = Source()
    service = CatalogService(source, clock=lambda: 1000)
    assert service.refresh("linear")
    result = service.query("linear", sort="price", direction="desc", limit=10)
    assert result["total"] == result["catalog"]["loaded"] == 1200
    assert result["catalog"]["completeness"] == "EXHAUSTED"
    assert result["catalog"]["expected_total"] is None
    assert result["rows"][0]["symbol"] == "S1199"
    assert result["rows"][0]["metrics"]["price"]["value"] == "999999"
    assert result["rows"][0]["metrics"]["market_cap"]["state"] == "UNAVAILABLE"
    assert result["rows"][0]["metrics"]["price_change_1h"]["value"] is None
    assert len(source.calls) == 7
    assert source.calls[1][1]["cursor"] == "next"


def test_failure_retains_good_snapshot_but_reports_stale_and_attempt_error():
    source = Source()
    now = [1000]
    service = CatalogService(source, clock=lambda: now[0])
    service.refresh("linear")
    before = service.query("linear")
    source.fail = True
    now[0] += 301
    assert not service.refresh("linear")
    after = service.query("linear")
    assert after["revision"] == before["revision"]
    assert after["rows"][0]["symbol"] == before["rows"][0]["symbol"]
    assert after["catalog"]["state"] == "STALE"
    assert after["catalog"]["last_error"] == "source_failure"
    assert after["catalog"]["last_success"] == 1000
    assert after["attempt"]["completeness"] == "UNKNOWN"


def test_ticker_failure_preserves_completed_catalog_acquisition_evidence():
    source = Source()
    original = source.call

    def failing_tickers(endpoint, params):
        if endpoint == "/tickers":
            raise AcquisitionError("rate_limited")
        return original(endpoint, params)

    source.call = failing_tickers
    service = CatalogService(source)
    assert not service.refresh("linear")
    attempt = service.query("linear")["attempt"]
    assert attempt["stage"] == "tickers"
    assert attempt["pages"] == 6
    assert attempt["status_coverage"]["Trading"]["completeness"] == "EXHAUSTED"


def test_cycle_is_partial_never_published_as_complete():
    source = Source()
    source.cycle = True
    service = CatalogService(source)
    assert not service.refresh("linear")
    result = service.query("linear")
    assert result["catalog"]["completeness"] == "UNKNOWN"
    assert result["attempt"]["completeness"] == "PARTIAL"
    assert result["attempt"]["loaded"] == 1200
    assert result["catalog"]["last_error"] == "repeated_cursor"
    assert result["rows"] == []


def test_category_isolation_revision_guard_filter_before_page_and_copy():
    service = CatalogService(Source())
    service.refresh("linear")
    result = service.query("linear", search="1199", limit=1)
    assert result["total"] == 1
    result["rows"][0]["symbol"] = "tampered"
    assert service.query("linear", search="1199")["rows"][0]["symbol"] == "S1199"
    assert service.query("inverse")["rows"] == []
    with pytest.raises(SnapshotConflict):
        service.query("linear", revision="old")
    with pytest.raises(ValueError):
        service.query("linear", limit=10001)


def test_nulls_last_in_both_directions_and_stable_ties():
    service = CatalogService(Source())
    service.refresh("linear")
    for direction, first in [("asc", "S0000"), ("desc", "S1199")]:
        rows = service.query("linear", sort="price", direction=direction, limit=3)["rows"]
        assert rows[0]["symbol"] == first
        assert rows[2]["metrics"]["price"]["value"] is None
    rows = service.query("linear", sort="market_cap", direction="desc", limit=3)["rows"]
    assert [row["symbol"] for row in rows] == ["S0000", "S0001", "S0002"]


def test_page_budget_missing_cursor_and_malformed_metrics_fail_truthfully():
    source = Source()
    service = CatalogService(source, max_pages=1)
    assert not service.refresh("linear")
    assert service.query("linear")["catalog"]["last_error"] == "page_budget"
    original = source.call

    def missing_cursor(endpoint, params):
        result, url = original(endpoint, params)
        result.pop("nextPageCursor", None)
        return result, url

    source.call = missing_cursor
    assert not service.refresh("inverse")
    assert service.query("inverse")["catalog"]["last_error"] == "cursor_missing"


def test_no_overlapping_refresh_and_cancellation_before_publication():
    entered, release = threading.Event(), threading.Event()
    source = Source()
    original = source.call

    def blocking(endpoint, params):
        entered.set()
        assert release.wait(3)
        return original(endpoint, params)

    source.call = blocking
    service = CatalogService(source)
    worker = threading.Thread(target=service.refresh, args=("linear",))
    worker.start()
    try:
        assert entered.wait(3)
        assert not service.refresh("linear")
        service._cancel.set()
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert service.query("linear")["rows"] == []
    assert service.query("linear")["catalog"]["last_error"] == "cancelled"


def test_single_backend_poller_categories_coalescing_and_graceful_stop():
    async def scenario():
        source = Source()
        service = CatalogService(source)
        await service.start()
        with pytest.raises(RuntimeError):
            await service.start()
        for _ in range(200):
            if service.query("inverse")["revision"]:
                break
            await asyncio.sleep(0.01)
        assert service.query("linear")["revision"]
        assert service.query("inverse")["revision"]
        for _ in range(100):
            service.request_refresh("linear")
        await asyncio.sleep(0.05)
        assert len(source.calls) == 14  # One category snapshot, not one per request/tab.
        await service.stop()
        assert service._task is None
        with pytest.raises(RuntimeError):
            service.request_refresh("linear")

    asyncio.run(scenario())


def test_opt_in_real_http_router_etag_revision_and_validation():
    service = CatalogService(Source(), clock=lambda: 1000)
    service.refresh("linear")
    app = FastAPI()
    app.include_router(create_market_router(service))
    with TestClient(app) as client:
        response = client.get("/api/market-catalog/linear?sort=price&direction=desc&limit=1")
        assert response.status_code == 200
        assert response.json()["data"]["rows"][0]["symbol"] == "S1199"
        assert (
            client.get(
                "/api/market-catalog/linear?sort=price&direction=desc&limit=1",
                headers={"If-None-Match": response.headers["etag"]},
            ).status_code
            == 304
        )
        assert client.get("/api/market-catalog/linear?revision=old").status_code == 409
        assert client.get("/api/market-catalog/linear?sort=bogus").status_code == 422
        assert client.get("/api/market-catalog/spot").status_code == 422


def test_cancellation_during_last_ticker_request_prevents_publication():
    source = Source()
    service = CatalogService(source)
    original = source.call

    def cancel(endpoint, params):
        result = original(endpoint, params)
        if endpoint == "/tickers":
            service._cancel.set()
        return result

    source.call = cancel
    assert not service.refresh("linear")
    assert service.query("linear")["revision"] is None


def test_public_source_bounds_response_body_before_json_decode(monkeypatch):
    class LargeResponse:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield b"x" * 8_000_001

        def json(self):
            pytest.fail("Unbounded response decoded")

    monkeypatch.setattr(
        "src.api.bybit_catalog._public_get", lambda *args, **kwargs: LargeResponse()
    )
    with pytest.raises(AcquisitionError, match="response_budget"):
        BybitCatalogSource().call("/tickers", {"category": "linear"})


def test_source_rate_limit_is_safe_and_source_timestamp_preserved(monkeypatch):
    class PublicResponse:
        status_code = 200
        url = "https://api.bybit.com/v5/market/tickers?category=linear"
        body = {"retCode": 10006}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield json.dumps(self.body).encode()

    monkeypatch.setattr(
        "src.api.bybit_catalog._public_get", lambda *args, **kwargs: PublicResponse()
    )
    with pytest.raises(AcquisitionError, match="rate_limited"):
        BybitCatalogSource().call("/tickers", {"category": "linear"})
    PublicResponse.body = {
        "retCode": 0,
        "time": 123456789,
        "result": {"category": "linear", "list": []},
    }
    result, _ = BybitCatalogSource().call("/tickers", {"category": "linear"})
    assert result["_source_time_ms"] == 123456789


def test_freshness_expiration_changes_etag_and_retains_metric_provenance():
    now = [1000]
    service = CatalogService(Source(), clock=lambda: now[0])
    service.refresh("linear")
    app = FastAPI()
    app.include_router(create_market_router(service))
    with TestClient(app) as client:
        url = "/api/market-catalog/linear?sort=price&direction=desc&limit=1"
        current = client.get(url)
        now[0] += 300
        stale = client.get(url, headers={"If-None-Match": current.headers["etag"]})
        assert stale.status_code == 200
        assert stale.headers["etag"] != current.headers["etag"]
        metric = stale.json()["data"]["rows"][0]["metrics"]["price"]
        assert metric["state"] == "STALE"
        assert metric["value"] == "999999"
        assert metric["observed_at"] == 1000
        assert metric["source"] == "https://api.bybit.com/v5/market/tickers"


@pytest.mark.parametrize(
    "stamp,state,reason",
    [
        (None, "UNKNOWN", "source_time_missing"),
        ("1000000", "UNKNOWN", "source_time_invalid"),
        (True, "UNKNOWN", "source_time_invalid"),
        (0, "UNKNOWN", "source_time_invalid"),
        (2000000, "UNKNOWN", "source_time_future"),
        (1, "STALE", "source_time_stale"),
        (700000, "STALE", "source_time_stale"),
        (999000, "OBSERVED", None),
    ],
)
def test_exchange_time_qualifies_metric_freshness(stamp, state, reason):
    source = Source()
    original = source.call

    def timestamp(endpoint, params):
        result, url = original(endpoint, params)
        if endpoint == "/tickers":
            result["_source_time_ms"] = stamp
        return result, url

    source.call = timestamp
    service = CatalogService(source, clock=lambda: 1000)
    assert service.refresh("linear")
    result = service.query("linear", sort="price", direction="desc", limit=1)
    metric = result["rows"][0]["metrics"]["price"]
    assert metric["value"] == "999999"
    assert metric["state"] == state
    assert metric["reason"] == reason
    if stamp is not None and (type(stamp) is not int or stamp <= 0):
        assert metric["source_time_ms"] is None
    assert result["catalog"]["state"] == {"OBSERVED": "CURRENT"}.get(state, state)


def test_cancel_during_normalization_is_final_publication_barrier():
    service = None

    def clock():
        service._cancel.set()
        return 1000

    service = CatalogService(Source(), clock=clock)
    assert not service.refresh("linear")
    assert service.query("linear")["revision"] is None


def test_manual_refresh_waits_for_30_second_budget_instead_of_being_lost(monkeypatch):
    async def scenario():
        now = [0.0]
        service = CatalogService(
            Source(), clock=lambda: 1000, monotonic=lambda: now[0], jitter=lambda: 0
        )
        deadlines = []

        async def wait(event_wait, timeout):
            event_wait.close()
            deadlines.append(timeout)
            if len(deadlines) == 1:
                now[0] = 5
                service.request_refresh("linear")
            elif len(deadlines) == 2:
                assert "linear" in service._manual
                assert timeout == 25
                now[0] = 30
            else:
                service._cancel.set()

        monkeypatch.setattr("src.api.market_catalog.asyncio.wait_for", wait)
        service._wake = asyncio.Event()
        await service._poll()
        linear = [
            call
            for call in service.source.calls
            if call[0] == "/tickers" and call[1]["category"] == "linear"
        ]
        inverse = [
            call
            for call in service.source.calls
            if call[0] == "/tickers" and call[1]["category"] == "inverse"
        ]
        assert len(linear) == 2
        assert len(inverse) == 1
        assert "linear" not in service._manual

    asyncio.run(scenario())


def test_restart_cannot_clear_cancellation_while_stop_waits_for_refresh():
    async def scenario():
        entered, release = threading.Event(), threading.Event()
        source = Source()
        original = source.call

        def blocking(endpoint, params):
            if endpoint == "/tickers" and not entered.is_set():
                entered.set()
                assert release.wait(3)
            return original(endpoint, params)

        source.call = blocking
        service = CatalogService(source)
        await service.start()
        assert await asyncio.to_thread(entered.wait, 3)
        stopping = asyncio.create_task(service.stop())
        await asyncio.sleep(0)
        starting = asyncio.create_task(service.start())
        await asyncio.sleep(0)
        try:
            assert not starting.done()
            assert service._cancel.is_set()
        finally:
            release.set()
            await stopping
            await starting
            await service.stop()

    asyncio.run(scenario())


def test_manual_refresh_cannot_bypass_rate_limit_backoff(monkeypatch):
    async def scenario():
        now = [0.0]
        source = Source()
        original = source.call
        attempts = []

        def limited(endpoint, params):
            if (
                endpoint == "/instruments-info"
                and params["category"] == "linear"
                and params["status"] == "Trading"
                and "cursor" not in params
            ):
                attempts.append(now[0])
                if len(attempts) == 1:
                    raise AcquisitionError("rate_limited")
            return original(endpoint, params)

        source.call = limited
        service = CatalogService(
            source, clock=lambda: 1000, monotonic=lambda: now[0], jitter=lambda: 0
        )
        deadlines = []

        async def wait(event_wait, timeout):
            event_wait.close()
            deadlines.append(timeout)
            if len(deadlines) == 1:
                now[0] = 5
                service.request_refresh("linear")
            elif len(deadlines) == 2:
                assert timeout == 295  # Inverse normal refresh precedes linear's backoff.
                assert "linear" in service._manual
                now[0] = 300
            elif len(deadlines) == 3:
                assert attempts == [0]
                assert timeout == 300
                now[0] = 600
            else:
                service._cancel.set()

        monkeypatch.setattr("src.api.market_catalog.asyncio.wait_for", wait)
        service._wake = asyncio.Event()
        await service._poll()
        assert attempts == [0, 600]

    asyncio.run(scenario())
