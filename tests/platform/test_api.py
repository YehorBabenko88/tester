from fastapi.testclient import TestClient

from src.api.config import Settings
from src.api.main import create_app


def client(tmp_path):
    return TestClient(create_app(Settings(data_dir=tmp_path)), base_url="http://127.0.0.1:8765")


def test_missing_storage_is_unhealthy_without_exposing_secrets(tmp_path):
    with client(tmp_path) as c:
        response = c.get("/api/health")
        assert response.status_code == 503
        assert response.json()["schema_version"] == "1.0"
        assert response.json()["data"]["database"] == "unavailable"
        assert "password" not in response.text


def test_unconnected_providers_are_explicit_not_empty_success(tmp_path):
    with client(tmp_path) as c:
        for endpoint in [
            "instruments",
            "sources",
            "coverage",
            "candles",
            "trades",
            "footprint",
            "open-interest",
            "funding",
        ]:
            response = c.get(
                "/api/" + endpoint, params={"symbol": "BTCUSDT", "start_ms": 0, "end_ms": 60000}
            )
            assert response.status_code == 503, response.text
            assert response.json()["error"]["code"] == "provider_unavailable"


def test_reject_ranges_limits_and_invalid_downloads(tmp_path):
    with client(tmp_path) as c:
        assert c.get("/api/candles?start_ms=100&end_ms=99").status_code == 422
        assert c.get("/api/trades?limit=1001").status_code == 422
        assert c.post("/api/downloads", json={}).status_code == 422
        assert (
            c.post(
                "/api/downloads",
                json={
                    "category": "linear",
                    "symbol": "../bad",
                    "source": "bybit",
                    "kind": "candles",
                    "interval": "60",
                    "start_ms": 0,
                    "end_ms": 60000,
                },
            ).status_code
            == 422
        )


def test_cross_site_mutation_and_rebinding_are_blocked(tmp_path):
    with client(tmp_path) as c:
        assert (
            c.post("/api/downloads", headers={"Origin": "https://evil.test"}, json={}).status_code
            == 403
        )
        assert c.get("/api/health", headers={"Host": "evil.test"}).status_code == 400


def test_provider_response_and_contract_paths(tmp_path):
    from canonical_fixtures import envelope, trade

    row = trade()
    for key in [
        "timestamp_ns",
        "timestamp_precision",
        "trade_id",
        "id_origin",
        "price",
        "quantity",
        "taker_side",
    ]:
        row.pop(key)
    row.update(status="Trading", tick_size="0.01", qty_step="0.001")
    expected = envelope(row)

    class Provider:
        def read(self, resource, query):
            return expected

    with TestClient(
        create_app(Settings(data_dir=tmp_path), provider=Provider()),
        base_url="http://127.0.0.1:8765",
    ) as c:
        assert c.get("/api/instruments").json() == expected
        paths = c.get("/openapi.json").json()["paths"]
        assert "/api/downloads/{id}/pause" in paths
        assert "/api/funding" in paths
