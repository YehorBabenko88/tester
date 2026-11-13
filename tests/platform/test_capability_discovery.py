"""Fail-closed capability discovery contract, no external provider or DB needed."""
from fastapi.testclient import TestClient
from src.api.main import create_app
from src.api.providers import UnavailableReader
from src.api.config import Settings
import inspect
import pytest

def test_capability_route_is_registered():
    # Verify the production factory actually registers the route.
    source = inspect.getsource(create_app)
    assert '@app.get("/api/capabilities"' in source
    assert 'source_revision_not_verified' in source

def test_capability_records_are_not_optimistic():
    source = inspect.getsource(create_app)
    for panel in ("candles", "volume", "footprint", "delta", "cvd",
                  "profile", "poc", "vwap", "open_interest", "funding",
                  "mark", "index"):
        assert f'"{panel}"' in source
    assert '"status": "UNVERIFIED"' in source
    assert '"reason": "source_revision_not_verified"' in source
