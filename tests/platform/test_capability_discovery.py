"""API capability discovery: explicit provenance and fail-closed defaults."""

import pytest
from fastapi.testclient import TestClient

from src.api.config import Settings
from src.api.main import create_app
from src.api.providers import UnavailableReader

PANELS = (
    "candles", "volume", "footprint", "delta", "cvd", "profile",
    "poc", "vwap", "open_interest", "funding", "mark", "index",
)
PARAMS = {
    "category": "linear", "symbol": "BTCUSDT", "interval": "60",
    "start_ms": 1700000000000, "end_ms": 1700003600000,
}


def client(reader):
    return TestClient(create_app(Settings(market_catalog_enabled=False), provider=reader))


def test_unavailable_reader_returns_all_panels_without_503():
    response = client(UnavailableReader()).get("/api/capabilities", params=PARAMS)
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "1.0"
    panels = payload["data"]["panels"]
    assert set(panels) == set(PANELS)
    assert all(item["status"] == "UNVERIFIED" for item in panels.values())
    assert all(item["reason"] == "source_revision_not_verified" for item in panels.values())
    assert all("resource" not in item for item in panels.values())


def test_reader_capability_requires_route_and_revision():
    class Reader:
        def capabilities(self, query):
            assert query.symbol == "BTCUSDT"
            return {
                "mark": {
                    "status": "AVAILABLE", "resource": "/api/candles",
                    "revision": "snapshot:42", "source_ref": "bybit:mark:42",
                },
                "index": {
                    "status": "AVAILABLE", "resource": "/api/analytics",
                    "revision": "snapshot:42", "source_ref": "bybit:index:42",
                },
                "funding": {
                    "status": "PARTIAL", "resource": "/api/funding",
                    "revision": "", "source_ref": "bybit:funding:42",
                },
            }

    response = client(Reader()).get("/api/capabilities", params=PARAMS)
    assert response.status_code == 200
    panels = response.json()["data"]["panels"]
    assert panels["mark"]["status"] == "AVAILABLE"
    assert panels["mark"]["resource"] == "/api/candles"
    assert panels["index"]["status"] == "UNVERIFIED"
    assert panels["funding"]["status"] == "UNVERIFIED"


def test_bad_scope_returns_schema_validation_error():
    response = client(UnavailableReader()).get(
        "/api/capabilities", params={**PARAMS, "end_ms": PARAMS["start_ms"]}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"

@pytest.mark.parametrize("interval", [
    "1", "3", "5", "15", "30", "60", "120",
    "240", "360", "720", "D", "W", "M",
])
def test_discovery_for_each_timeframe_fails_closed(interval):
    response = client(UnavailableReader()).get(
        "/api/capabilities", params={**PARAMS, "interval": interval}
    )
    assert response.status_code == 200
    panels = response.json()["data"]["panels"]
    assert len(panels) == 12
    assert all(p["status"] == "UNVERIFIED" for p in panels.values())
