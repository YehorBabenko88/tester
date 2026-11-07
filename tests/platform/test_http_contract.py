from copy import deepcopy

import pytest
from canonical_fixtures import envelope
from fastapi.testclient import TestClient

from src.api.config import Settings
from src.api.main import create_app


@pytest.mark.timeout(10)
def test_http_canonical_roundtrip_and_reject_invalid_provider(tmp_path):
    class Provider:
        value = envelope()

        def read(self, resource, query):
            return deepcopy(self.value)

    provider = Provider()
    with TestClient(
        create_app(Settings(data_dir=tmp_path), provider), base_url="http://127.0.0.1:8765"
    ) as client:
        response = client.get("/api/trades")
        assert response.status_code == 200
        assert response.json() == provider.value
        provider.value["data"][0]["schema_version"] = "wrong"
        response = client.get("/api/trades")
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "provider_contract_error"
        assert "wrong" not in response.text


@pytest.mark.timeout(10)
def test_http_ms_source_and_unknown_side_remain_truthful(tmp_path):
    value = envelope()
    row = value["data"][0]
    row.update(timestamp_ns=None, timestamp_precision="ms")
    row["provenance"][0].update(timestamp_precision="ms", source_timestamp="1700000000123")
    row["availability"].update(status="UNAVAILABLE", reason="source_conflict")
    value["availability"] = deepcopy(row["availability"])
    value["provenance"] = deepcopy(row["provenance"])

    class Provider:
        def read(self, resource, query):
            return value

    with TestClient(
        create_app(Settings(data_dir=tmp_path), Provider()), base_url="http://127.0.0.1:8765"
    ) as client:
        response = client.get("/api/trades")
        assert response.status_code == 200
        assert response.json() == value


@pytest.mark.timeout(10)
def test_observation_cannot_be_served_under_wrong_resource(tmp_path):
    value = envelope()
    row = value["data"][0]
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
    row.update(kind="oi", value="100", unit="contracts", interval="5min")

    class Provider:
        def read(self, resource, query):
            return value

    with TestClient(
        create_app(Settings(data_dir=tmp_path), Provider()), base_url="http://127.0.0.1:8765"
    ) as client:
        assert client.get("/api/open-interest").status_code == 200
        assert client.get("/api/funding").status_code == 502
