"""Bounded real source capability discovery through the production FastAPI route."""

import pytest
from fastapi.testclient import TestClient

from src.api.config import Settings
from src.api.main import create_app
from src.api.providers import ProviderUnavailable, UnavailableReader
from src.integration.reference_prices import ReferencePriceReader

INTERVALS = ("1", "3", "5", "15", "30", "60", "120", "240", "360", "720", "D", "W", "M")
PANELS = (
    "candles",
    "volume",
    "footprint",
    "delta",
    "cvd",
    "profile",
    "poc",
    "vwap",
    "open_interest",
    "funding",
    "mark",
    "index",
)
PARAMS = dict(category="linear", symbol="BTCUSDT", interval="1", start_ms=0, end_ms=60000)


def client(reader, *, reference=False):
    return TestClient(
        create_app(
            Settings(
                market_catalog_enabled=False,
                provider_factory=(
                    "src.integration.reference_prices:create_reader" if reference else None
                ),
            ),
            provider=reader,
        ),
        base_url="http://127.0.0.1:8765",
    )


def test_unconnected_discovery_returns_200_without_probing_unsupported_routes():
    with client(UnavailableReader()) as c:
        response = c.get("/api/capabilities", params=PARAMS)
    assert response.status_code == 200
    data = response.json()
    assert data["schema_version"] == "1.0"
    panels = data["data"]["panels"]
    assert set(panels) == set(PANELS)
    assert all(p["status"] not in ("AVAILABLE", "PARTIAL") for p in panels.values())
    assert all("resource" not in p for p in panels.values())


def test_unconfigured_reader_claims_cannot_authorize_a_source():
    class Liar:
        def capabilities(self, query):
            return {
                "mark": {
                    "status": "AVAILABLE",
                    "resource": "/api/candles",
                    "source_ref": "fake",
                    "revision": "fake",
                }
            }

        def read(self, resource, query):
            raise AssertionError("unconfigured source must never be probed")

    with client(Liar()) as c:
        response = c.get("/api/capabilities", params=PARAMS)
    assert response.status_code == 200
    assert response.json()["data"]["panels"]["mark"]["status"] == "UNVERIFIED"
    assert response.json()["data"]["panels"]["mark"]["reason"] == "source_not_authorized"


def reference_row(kind, *, source_override=None):
    endpoint = "mark-price-kline" if kind == "official_mark" else "index-price-kline"
    provenance = [
        dict(
            source_kind="bybit_rest",
            source_ref=source_override
            or (
                "https://api.bybit.com/v5/market/"
                + endpoint
                + "?category=linear&symbol=BTCUSDT&interval=1"
            ),
            source_hash="a" * 64,
            ingestion_run="verified-fixture-run",
            timestamp_precision="ms",
            source_timestamp="0",
        )
    ]
    row = dict(
        schema_version="1.1",
        venue="bybit",
        category="linear",
        symbol="BTCUSDT",
        timestamp_ms=0,
        start_ms=0,
        end_ms=60000,
        interval="1",
        kind=kind,
        open="100",
        high="101",
        low="99",
        close="100.5",
        is_closed=True,
        provenance=provenance,
        availability={"status": "PARTIAL", "reason": "Observed closed price; history unknown"},
        coverage={"status": "UNKNOWN", "start_ms": 0, "end_ms": 60000, "evidence_refs": []},
    )
    return row


class PriceReader(ReferencePriceReader):
    def __init__(self, *, corrupt=False):
        self.corrupt = corrupt
        self.calls = []

    def read(self, resource, query):
        self.calls.append((resource, query.candle_kind))
        if resource != "price-candles":
            raise ProviderUnavailable("Source profile does not provide other series")
        if query.interval != "1" or (query.start_ms, query.end_ms) != (0, 60000):
            return dict(
                schema_version="1.1",
                data=[],
                next_cursor=None,
                availability={"status": "UNAVAILABLE", "reason": "No observed prices"},
                coverage={
                    "status": "UNKNOWN",
                    "start_ms": query.start_ms,
                    "end_ms": query.end_ms,
                    "evidence_refs": [],
                },
                provenance=[],
            )
        source = "https://evil.example/fake" if self.corrupt else None
        row = reference_row(query.candle_kind, source_override=source)
        return dict(
            schema_version="1.1",
            data=[row],
            next_cursor=None,
            availability={"status": "PARTIAL", "reason": "Observed only"},
            coverage=row["coverage"],
            provenance=row["provenance"],
        )


def test_real_price_only_route_is_qualified_from_validated_records():
    reader = PriceReader()
    with client(reader, reference=True) as c:
        response = c.get("/api/capabilities", params=PARAMS)
        assert response.status_code == 200, response.text
        panels = response.json()["data"]["panels"]
        for panel in ("mark", "index"):
            item = panels[panel]
            assert item["status"] == "PARTIAL"
            assert item["reason"] == "bounded_observations_only"
            assert item["resource"] == "/api/price-candles"
            assert item["schema_version"] == "1.1"
            assert item["candle_kind"] == "official_" + panel
            assert item["revision"].startswith("page:")
            assert item["source_ref"].startswith("https://api.bybit.com/")
            response = c.get(
                "/api/price-candles",
                params={
                    **PARAMS,
                    "candle_kind": item["candle_kind"],
                    "schema_version": "1.1",
                    "limit": 1000,
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["schema_version"] == "1.1"
            assert response.json()["data"][0]["kind"] == item["candle_kind"]
            assert "volume" not in response.json()["data"][0]
        for panel in ("footprint", "delta", "cvd", "funding", "open_interest"):
            assert panels[panel]["status"] not in ("AVAILABLE", "PARTIAL")


def test_fake_price_source_is_not_qualified_even_when_contract_shape_is_valid():
    with client(PriceReader(corrupt=True), reference=True) as c:
        response = c.get("/api/capabilities", params=PARAMS)
    assert response.status_code == 200
    assert response.json()["data"]["panels"]["mark"]["status"] == "UNVERIFIED"
    assert "resource" not in response.json()["data"]["panels"]["mark"]


def test_spot_does_not_advertise_derivative_only_sources():
    with client(UnavailableReader()) as c:
        response = c.get("/api/capabilities", params={**PARAMS, "category": "spot"})
    assert response.status_code == 200
    for panel in ("mark", "index", "open_interest", "funding"):
        item = response.json()["data"]["panels"][panel]
        assert item["status"] == "UNAVAILABLE"
        assert item["reason"] == "not_applicable_to_spot"


@pytest.mark.parametrize("interval", INTERVALS)
def test_all_13_timeframes_return_valid_discovery_without_404_422_503(interval):
    with client(UnavailableReader()) as c:
        response = c.get("/api/capabilities", params={**PARAMS, "interval": interval})
    assert response.status_code == 200, response.text
    assert len(response.json()["data"]["panels"]) == 12


def test_invalid_range_is_rejected_and_untrusted_host_is_denied():
    with client(UnavailableReader()) as c:
        invalid = c.get("/api/capabilities", params={**PARAMS, "end_ms": 0})
        assert invalid.status_code == 422
        denied = c.get("/api/capabilities", params=PARAMS, headers={"Host": "evil.example"})
        assert denied.status_code == 400


def test_oi_discovery_uses_selected_source_interval_not_default():
    seen = []

    class SpyReader:
        def read(self, resource, query):
            if resource == "open-interest":
                seen.append((query.interval, query.source_interval, query.viewport_interval))
            raise ProviderUnavailable("Not connected")

    app = create_app(
        Settings(
            provider_factory="src.integration.lab_market:create_reader",
            market_catalog_enabled=False,
        ),
        provider=SpyReader(),
    )
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        response = c.get("/api/capabilities", params={**PARAMS, "source_interval": "1h"})
    assert response.status_code == 200
    assert seen == [("1h", "1h", "1")]
    assert response.json()["data"]["panels"]["open_interest"]["status"] == "UNVERIFIED"


def test_optional_routes_are_not_advertised_when_backend_not_configured():
    with client(UnavailableReader()) as c:
        response = c.get("/api/capabilities")
        assert response.status_code == 200
        features = response.json()["data"]["features"]
        assert features["market_catalog"] == {"enabled": False, "reason": "catalog_not_configured"}
        assert features["sources"]["enabled"] is False
        assert features["sources"]["reason"] == "catalog_and_provider_required"
        assert not any(route.path.startswith("/api/market-catalog") for route in c.app.routes)


def test_price_only_source_timestamp_must_match_original_closed_bar():
    class WrongTimestampReader(PriceReader):
        def read(self, resource, query):
            payload = super().read(resource, query)
            if resource == "price-candles" and payload["data"]:
                payload["provenance"][0]["source_timestamp"] = "123"
            return payload

    with client(WrongTimestampReader(), reference=True) as c:
        response = c.get("/api/capabilities", params=PARAMS)
    assert response.status_code == 200
    panels = response.json()["data"]["panels"]
    for panel in ("mark", "index"):
        assert panels[panel]["status"] == "UNVERIFIED"
        assert panels[panel]["reason"] == "price_source_timestamp_mismatch"
        assert "resource" not in panels[panel]
