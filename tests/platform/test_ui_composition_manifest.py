"""Static MASTER #77 UI handoff manifest and fail-closed route preflight.

No GitHub API, network, PostgreSQL, cross-branch composition or filesystem writes.
"""

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scripts.verify_ui_source_contracts import verify_shared_pg_hostaddr_test
from src.api.config import Settings
from src.api.main import create_app

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs/integration/ui74-master77-manifest.json"


def load_manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_exact_manifest_has_all_files_and_no_overwrites():
    handoff = load_manifest()
    assert handoff["schema_version"] == "tester.ui74-master77-file-manifest/1"
    assert handoff["status"] == "READY_FOR_COMPOSITION"
    assert handoff["composition"]["scope"] == "SOURCE_ONLY"
    assert handoff["composition"]["ready_for_composition"] is True
    assert handoff["composition"]["ready_for_deployment"] is False
    assert handoff["composition"]["master_pr77_composed"] is False
    assert handoff["composition"]["combined_head_ci"] == "NOT_RUN"
    assert handoff["composition"]["real_data_admission"] == "DENIED_UNKNOWN_COVERAGE"
    assert handoff["composition"]["ui_pr70_ancestry_required"] is True
    assert handoff["composition"]["pghostaddr_shared_test_blob"] == "402c25410ba4cc6704911148b1ec0c5bba720088"
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


def _git_blob(content: bytes) -> str:
    return hashlib.sha1(
        b"blob " + str(len(content)).encode("ascii") + b"\0" + content
    ).hexdigest()


def test_frozen_v1_openapi_git_blob_unchanged_by_ui_handoff():
    path = ROOT / "contracts/v1/openapi.json"
    assert _git_blob(path.read_bytes()) == "fc3f4b20d4bd6d6fed642f9e00ceb099c68c4398"
    schema = json.loads(path.read_text(encoding="utf-8"))
    assert schema["components"]["schemas"]["DownloadRequest"]["properties"]["kind"]["enum"] == [
        "candles", "trades", "footprint", "open_interest", "funding"
    ]
    handoff = load_manifest()
    assert handoff["openapi"]["status"] == "CONTRACT_OWNER_REVIEW_REQUIRED"
    assert handoff["openapi"]["unapproved_versioned_path"] == "/api/v1_1/downloads"


def test_common_pg_hostaddr_blob_matches_current_master_resolution(tmp_path):
    # Source-only stand-in; hosted CI independently checks the actual exact
    # DATA/RESEARCH Git blobs against 402c25410 and still runs PG tests.
    content = (
        "PGHOSTADDR PGHOST PGPORT PGDATABASE "
        "conn.info.hostaddr inet_server_addr 127.0.0.2 172.18.0.2 "
        "test_actual_postgres_hostaddr_environment_cannot_redirect "
        "test_research_dsn_refuses_explicit_hostaddr_redirect"
    ).encode("utf-8")
    data, research = tmp_path / "data.py", tmp_path / "research.py"
    data.write_bytes(content)
    research.write_bytes(content)
    report = verify_shared_pg_hostaddr_test(
        data, research, expected_blob=_git_blob(content)
    )
    assert report["identical_DATA_RESEARCH_bytes"] is True
    assert report["hostaddr_negative_guards_preserved"] is True
    assert report["runtime_postgres_acceptance"] == "NOT_RUN_BY_SOURCE_AUDIT"


def test_pg_hostaddr_last_writer_wins_and_missing_guard_rejected(tmp_path):
    content = (
        "PGHOSTADDR PGHOST PGPORT PGDATABASE "
        "conn.info.hostaddr inet_server_addr 127.0.0.2 172.18.0.2 "
        "test_actual_postgres_hostaddr_environment_cannot_redirect "
        "test_research_dsn_refuses_explicit_hostaddr_redirect"
    ).encode("utf-8")
    data, research = tmp_path / "data.py", tmp_path / "research.py"
    data.write_bytes(content)
    research.write_bytes(content + b" altered")
    with pytest.raises(ValueError, match="requires_manual_reconciliation"):
        verify_shared_pg_hostaddr_test(data, research, expected_blob=_git_blob(content))
    research.write_bytes(content)
    with pytest.raises(ValueError, match="unreviewed_git_blob"):
        verify_shared_pg_hostaddr_test(data, research, expected_blob="0" * 40)
    weak = content.replace(b"PGHOSTADDR", b"PG_DISABLED")
    data.write_bytes(weak)
    research.write_bytes(weak)
    with pytest.raises(ValueError, match="negative_controls_absent"):
        verify_shared_pg_hostaddr_test(data, research, expected_blob=_git_blob(weak))
