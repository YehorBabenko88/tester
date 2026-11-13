"""Backward-compatibility gate for an unactivated DownloadRequest v1.1 proposal."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from scripts.compare_download_kind_proposal import compare_contracts
from src.api.config import Settings
from src.api.main import create_app

ROOT = Path(__file__).resolve().parents[2]
PINNED = ROOT / "contracts/v1/openapi.json"
PROPOSAL = ROOT / "docs/proposals/download-kind-v1_1.json"
LEGACY_KINDS = ["candles", "trades", "footprint", "open_interest", "funding"]


def test_pinned_v1_download_clients_are_not_modified():
    legacy = json.loads(PINNED.read_text(encoding="utf-8"))
    proposal = json.loads(PROPOSAL.read_text(encoding="utf-8"))
    assert legacy["info"]["version"] == "1.0.0"
    assert legacy["components"]["schemas"]["DownloadRequest"]["properties"]["kind"]["enum"] == (
        LEGACY_KINDS
    )
    assert proposal["legacy_kinds"] == LEGACY_KINDS
    assert proposal["proposed_kinds"] == [*LEGACY_KINDS, "mark", "index"]
    assert proposal["activated"] is False
    assert proposal["constraints"]["existing_v1_request_schema_immutable"] is True
    assert proposal["constraints"]["new_route_requires_contract_owner_approval"] is True


def test_only_known_runtime_openapi_drift_is_kind_enum():
    report = compare_contracts()
    assert report["legacy_v1_unchanged"] is True
    assert report["runtime_equals_single_enum_delta"] is True
    assert report["pinned_kind_enum"] == LEGACY_KINDS
    assert report["runtime_kind_enum"] == [*LEGACY_KINDS, "mark", "index"]
    assert report["new_v1_1_endpoint_activated"] is False
    assert report["approval_required"] is True


def test_no_versioned_download_route_is_exposed_without_owner_approval():
    app = create_app(Settings(market_catalog_enabled=False))
    routes = {getattr(route, "path", "") for route in app.routes}
    assert "/api/downloads" in routes
    assert "/api/v1_1/downloads" not in routes
    assert "/api/analytics" not in routes
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.get("/api/capabilities")
        assert response.status_code == 200
        assert response.json()["data"]["protocol_version"] == "tester.ui-capabilities/1"
        assert response.json()["data"]["observed_page_revision"]["history_complete"] is False


def test_mark_index_price_only_source_contract_is_not_a_download_grant():
    proposal = json.loads(PROPOSAL.read_text(encoding="utf-8"))
    for kind in ("mark", "index"):
        source = proposal["source_types"][kind]
        assert source["price_only_schema"] == "1.1"
        assert source["reader"] == "/api/price-candles"
        assert source["kind"] == "official_" + kind
        assert source["volume"] == "NOT_APPLICABLE"
    assert proposal["constraints"]["no_implicit_mark_index_download_authorization"] is True
    assert proposal["constraints"]["historical_source_coverage"] == "UNKNOWN"
