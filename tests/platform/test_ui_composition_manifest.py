"""Static MASTER #77 UI handoff manifest and fail-closed route preflight.

No GitHub API, network, PostgreSQL, cross-branch composition or filesystem writes.
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from src.api.config import Settings
from src.api.main import create_app

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs/integration/ui74-master77-manifest.json"


def load_manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_exact_manifest_has_all_files_and_no_overwrites():
    handoff = load_manifest()
    assert handoff["schema_version"] == "tester.ui74-master77-file-manifest/1"
    assert handoff["status"] == "DRAFT_NO_GO"
    assert handoff["source"]["base_pr"] == 70
    assert handoff["source"]["base_head"] == "6240b6665fb3125c06eb91ef535b96700988a7ab"
    assert handoff["strategy"].startswith("Apply PR70 ancestor before")
    paths = handoff["all_changed"]
    assert len(paths) == handoff["total_changed"]
    assert len(paths) == len(set(paths))
    assert paths == sorted(paths)
    assert all((ROOT / path).is_file() for path in paths)
    groups = handoff["groups"]
    assert set(groups) == {"runtime", "tests_and_probes", "ci_and_documentation"}
    flat = [path for files in groups.values() for path in files]
    assert set(flat) == set(paths)
    assert len(flat) == len(paths)
    for path in handoff["pr70_included_and_updated"]:
        assert path in paths
        assert path.startswith("frontend/")
    for name in (
        "contracts/v1/openapi.json",
        "src/integration/research_ui_projection.py",
        "tests/platform/test_research_postgres.py",
    ):
        assert name not in paths
    assert handoff["overlap"]["ui74_overlap_with_data71_72_ops73"] == []
    assert handoff["overlap"]["data72_research71"] == ["tests/platform/test_research_postgres.py"]
    assert handoff["overlap"]["data72_research71_status"] == "RESOLVED_IDENTICAL_GIT_BLOB"
    assert handoff["overlap"]["data72_research71_shared_blob"] == (
        "402c25410ba4cc6704911148b1ec0c5bba720088"
    )


def test_manifest_research_entrypoint_stays_unmounted_and_f_denied():
    handoff = load_manifest()
    backend = handoff["backend"]
    assert backend["data"]["contract"] == "tester-public-execution/1"
    assert backend["research"]["ui_schema"] == "tester.research-ui-observation/1"
    assert backend["research"]["http_route"] is None
    assert handoff["admission"]["admitted_to_f"] is False
    assert handoff["admission"]["historical_coverage"] == "UNKNOWN"
    assert handoff["admission"]["ge_real_execution"] is False
    assert handoff["openapi"]["pinned_v1_immutable"] is True

    settings = Settings(market_catalog_enabled=False, frontend_dir=ROOT / "frontend")
    app = create_app(settings)
    routes = {getattr(route, "path", "") for route in app.routes}
    assert "/api/capabilities" in routes
    assert "/api/v1_1/downloads" not in routes
    assert "/api/analytics" not in routes
    assert not any(path.startswith("/api/market-catalog/") for path in routes)
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
    data = response.json()["data"]
    assert data["features"]["market_catalog"]["enabled"] is False
    assert data["features"]["sources"]["enabled"] is False
    assert data["observed_page_revision"]["history_complete"] is False
    assert data["observed_page_revision"]["supports_f_admission"] is False
    for panel in ("footprint", "delta", "cvd", "vwap", "poc", "profile"):
        assert data["panels"][panel]["reason"] == "endpoint_contract_not_supported"
        assert "resource" not in data["panels"][panel]
    for panel in ("open_interest", "funding"):
        assert data["panels"][panel]["status"] != "AVAILABLE"
        assert "resource" not in data["panels"][panel]


def test_legacy_download_v1_is_not_rewritten_by_handoff():
    document_path = ROOT / "contracts/v1/openapi.json"
    schema = json.loads(document_path.read_text(encoding="utf-8"))
    kinds = schema["components"]["schemas"]["DownloadRequest"]["properties"]["kind"]["enum"]
    assert kinds == [
        "candles",
        "trades",
        "footprint",
        "open_interest",
        "funding",
    ]
    assert load_manifest()["openapi"]["status"] == "CONTRACT_OWNER_REVIEW_REQUIRED"
