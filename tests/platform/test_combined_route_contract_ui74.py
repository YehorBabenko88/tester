"""CI-only route graph regression for UI74 and the combined synthetic G/E app.

Do not replace this with a browser mock or activate a native runtime.
"""

from pathlib import Path

from fastapi.testclient import TestClient
from starlette.routing import Mount

from src.api.config import Settings
from src.api.main import create_app
from src.integration.combined_routes import compose_research


ROOT = Path(__file__).resolve().parents[2]
INTERVALS = ("1", "3", "5", "15", "30", "60", "120", "240", "360", "720", "D", "W", "M")


def _fixture_market():
    return create_app(
        Settings(
            market_catalog_enabled=False,
            frontend_dir=ROOT / "frontend",
            data_dir=ROOT / ".test-ui74-unused-data",
        )
    )


def test_composed_browser_capabilities_precede_static_frontend_and_keep_13tf():
    market = _fixture_market()
    routes = list(market.router.routes)
    capability = [
        i for i, item in enumerate(routes) if getattr(item, "path", "") == "/api/capabilities"
    ]
    frontend = [i for i, item in enumerate(routes) if isinstance(item, Mount) and item.path == ""]
    assert len(capability) == 1, "missing UI74 GET /api/capabilities"
    assert frontend and capability[0] < frontend[0], (
        "frontend catch-all shadows capability route"
    )
    with TestClient(market, base_url="http://127.0.0.1:8765") as client:
        for interval in INTERVALS:
            result = client.get(
                "/api/capabilities",
                params={
                    "category": "linear",
                    "symbol": "BTCUSDT",
                    "interval": interval,
                    "start_ms": 1704067200000,
                    "end_ms": 1711929600000,
                },
            )
            assert result.status_code == 200, (interval, result.text)
            data = result.json()["data"]
            assert data["protocol_version"] == "tester.ui-capabilities/1"
            assert not data["observed_page_revision"]["history_complete"]
            assert not data["observed_page_revision"]["supports_f_admission"]
            for panel in ("footprint", "delta", "cvd", "vwap", "poc", "profile"):
                assert data["panels"][panel]["status"] == "UNAVAILABLE"
                assert "resource" not in data["panels"][panel]
            for panel in ("mark", "index", "open_interest", "funding"):
                assert data["panels"][panel]["status"] == "UNVERIFIED"
                assert "resource" not in data["panels"][panel]


def test_legacy_market_is_incompatible_with_new_combined_browser():
    from fastapi import FastAPI

    older_market = FastAPI()
    route_paths = {getattr(item, "path", "") for item in older_market.routes}
    assert "/api/capabilities" not in route_paths
    assert "/api/capabilities" in {
        getattr(item, "path", "") for item in _fixture_market().routes
    }


def test_research_routes_are_before_static_mount_without_security_bypass():
    from src.api.research_control import create_control_app

    market = _fixture_market()
    research = create_control_app(None, lambda _authorization: None)
    combined = compose_research(market, research)
    routes = list(combined.router.routes)
    assert [getattr(x, "path", "") for x in routes[:2]] == [
        "/api/control/research",
        "/api/control/research/{rest:path}",
    ]
    caps = [i for i, x in enumerate(routes) if getattr(x, "path", "") == "/api/capabilities"]
    assert caps and caps[0] > 1
    mount = [i for i, x in enumerate(routes) if isinstance(x, Mount) and x.path == ""]
    assert mount and caps[0] < mount[0]
    assert not any(getattr(x, "path", "") == "/api/analytics" for x in routes)
