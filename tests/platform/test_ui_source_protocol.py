"""Exact opt-in UI protocol boundary for the independent DATA/RESEARCH drafts."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from src.api.capability_source_contracts import (
    DATA_EXECUTION_VERSION,
    RESEARCH_METRICS_VERSION,
    dependencies_for_panel,
    manifest,
)
from src.api.config import Settings
from src.api.main import create_app

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "docs/ui-capability-protocol-v1.schema.json").read_text(
        encoding="utf-8"
    )
)
INTERVALS = ("1", "3", "5", "15", "30", "60", "120", "240", "360", "720", "D", "W", "M")


@pytest.fixture(scope="module")
def validator():
    Draft202012Validator.check_schema(SCHEMA)
    return Draft202012Validator(SCHEMA)


@pytest.mark.parametrize("interval", INTERVALS)
def test_real_fastapi_protocol_schema_has_twelve_fail_closed_panels(interval, validator):
    app = create_app(Settings(market_catalog_enabled=False))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.get(
            "/api/capabilities",
            params={
                "category": "linear",
                "symbol": "BTCUSDT",
                "interval": interval,
                "start_ms": 0,
                "end_ms": 86400000,
            },
        )
    assert response.status_code == 200
    validator.validate(response.json())
    data = response.json()["data"]
    assert data["protocol_version"] == "tester.ui-capabilities/1"
    assert data["observed_page_revision"]["historical_dataset_revision"] == "UNPINNED"
    assert data["observed_page_revision"]["supports_f_admission"] is False
    for panel in data["panels"].values():
        assert panel["status"] not in {"AVAILABLE", "PARTIAL"}
        assert "resource" not in panel
        assert "revision" not in panel


def test_data72_research71_version_and_exact_call_seam():
    value = manifest()
    source = value["source_contracts"]["public_executions"]
    research = value["source_contracts"]["observed_execution_metrics"]
    aux = value["source_contracts"]["public_auxiliary"]
    assert source["contract_version"] == DATA_EXECUTION_VERSION == "tester-public-execution/1"
    assert source["producer"] == "src.ingest.execution_contract.read_execution_events"
    assert source["research_projection"] == "src.ingest.execution_contract.read_research_trades"
    assert source["max_rows"] == 100
    assert source["max_range_ms"] == 7 * 86400000
    assert source["quantity_unit"] == "source_native_linear_size"
    assert source["event_time"] == "event_ms"
    assert source["observation_time"] == "observed_ms"
    assert source["publication_time"] == "UNKNOWN"
    assert source["source_coverage"] == "UNKNOWN"
    assert research["contract_version"] == RESEARCH_METRICS_VERSION
    assert research["consumer"] == "src.integration.data_research_adapter.read_data_metrics"
    assert research["requires"] == DATA_EXECUTION_VERSION
    assert research["admitted_to_f"] is False
    assert research["historical_visibility"] == "UNKNOWN"
    assert aux["readback_state"] == "NOT_BOUND_TO_UI_ROUTE"
    assert aux["native_oi_cadences"] == ["5min", "15min", "30min", "1h", "4h", "1d"]
    assert aux["funding_cadence"] == "event"


def test_dependencies_cannot_grant_a_nonexistent_analytics_route():
    app = create_app(Settings(market_catalog_enabled=False))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.get(
            "/api/capabilities",
            params={
                "category": "linear",
                "symbol": "BTCUSDT",
                "interval": "1",
                "start_ms": 0,
                "end_ms": 60000,
            },
        )
    assert response.status_code == 200
    panels = response.json()["data"]["panels"]
    for kind in ("footprint", "delta", "cvd", "vwap", "poc", "profile"):
        assert panels[kind]["source_contract_dependencies"] == dependencies_for_panel(kind)
        assert panels[kind]["reason"] == "endpoint_contract_not_supported"
        assert panels[kind]["historical_coverage"] == "UNKNOWN"
        assert panels[kind]["admitted_to_f"] is False
        assert "resource" not in panels[kind]
    for kind in ("open_interest", "funding"):
        assert panels[kind]["source_contract_dependencies"] == ["public_auxiliary"]
        assert panels[kind]["status"] != "AVAILABLE"
        assert "resource" not in panels[kind]
    assert "/api/analytics" not in {getattr(route, "path", "") for route in app.routes}


def test_manifest_is_detached_and_never_promotes_history():
    first = manifest()
    first["source_contracts"]["public_executions"]["source_coverage"] = "COMPLETE"
    first["observed_page_revision"]["supports_f_admission"] = True
    fresh = manifest()
    assert fresh["source_contracts"]["public_executions"]["source_coverage"] == "UNKNOWN"
    assert fresh["observed_page_revision"]["supports_f_admission"] is False
