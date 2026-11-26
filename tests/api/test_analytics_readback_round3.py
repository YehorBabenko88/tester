"""Fail-closed tests for the unmounted read-only DATA analytics adapter.

These do not claim PostgreSQL acceptance or authenticated installation binding.
"""
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.api.analytics_readback import ReadbackScope, create_readback_router, observed_readback


def scope(**overrides):
    args = dict(category="inverse", symbol="BTCUSD", start_ms=0, end_ms=60_000,
                interval_ms=60_000, tick_size="1", as_of_ms=60_000, limit=99)
    args.update(overrides)
    return ReadbackScope(**args)


def identity(**overrides):
    x = dict(verified=True, installation_id=str(uuid4()), source_sha="a" * 40)
    x.update(overrides)
    return x


@pytest.mark.parametrize("overrides", [
    {"category": "spot"}, {"symbol": "A"}, {"symbol": "BTC-USD"},
    {"start_ms": -1}, {"start_ms": 60_000}, {"end_ms": 2 * 86_400_000 * 7},
    {"interval_ms": 90_000}, {"interval_ms": 0}, {"as_of_ms": 59_999},
    {"tick_size": "0"}, {"tick_size": "NaN"}, {"tick_size": "1e-8"},
    {"limit": 100}, {"limit": 0}, {"limit": True},
])
def test_invalid_scope_fails_before_storage(overrides):
    with pytest.raises(ValidationError):
        scope(**overrides)


def test_default_route_denies_without_trusted_binding():
    app = FastAPI()
    app.include_router(create_readback_router())
    client = TestClient(app)
    response = client.get("/api/analytics/readback", params=scope().model_dump())
    assert response.status_code == 503
    assert response.json() == {"detail": "analytics_source_not_configured"}


def test_route_refuses_client_owned_installation_scope():
    app = FastAPI()
    app.include_router(create_readback_router())
    params = scope().model_dump()
    params.update(installation_id=str(uuid4()), database_url="postgresql://untrusted")
    response = TestClient(app).get("/api/analytics/readback", params=params)
    assert response.status_code == 422


def test_missing_bad_ownership_refuses_before_calculation(monkeypatch):
    from src.api import analytics_readback as module
    def forbidden(*args, **kwargs):
        raise AssertionError("DATA backend must not be read without verified authority")
    monkeypatch.setattr(module, "read_verified_ui_observation", forbidden)
    class StubStore:
        def read_bounded(self, *args, **kwargs):
            raise AssertionError("No read expected for untrusted identity")
    for supplied in [None, {}, identity(verified=False),
                     identity(installation_id="not-a-uuid"),
                     identity(source_sha="../bad")]:
        result = observed_readback(scope(), trusted_store=StubStore(), trusted_identity=supplied)
        assert result["status"] == "UNVERIFIED"
        assert result["admitted_to_f"] is False


def test_trusted_but_incomplete_canonical_readback_never_grants_f(monkeypatch):
    from src.api import analytics_readback as module
    seen = []
    def source(store, **kwargs):
        seen.append((store, kwargs))
        return dict(schema_version="tester.research-ui-observation/1",
                    status="OBSERVED_UNVERIFIED",
                    f_admission="DENIED_UNKNOWN_COVERAGE",
                    source_coverage="UNKNOWN", publication_time="UNKNOWN",
                    method="tester.execution-metrics/2", quantity_unit="quote",
                    source_quantity_unit="source_native_inverse_contract",
                    utc_window={"start_ms": 0, "end_ms": 60_000},
                    source_receipts_digest="b" * 64,
                    observed_sample_fingerprint="c" * 64,
                    bars=[{"start_ms": 0, "end_ms": 60_000,
                           "volume": "100", "vwap": "100"}])
    monkeypatch.setattr(module, "read_verified_ui_observation", source)
    class StubStore:
        def read_bounded(self, *args, **kwargs):
            return []
    store = StubStore()
    result = observed_readback(scope(), trusted_store=store, trusted_identity=identity())
    assert result["status"] == "OBSERVED_UNVERIFIED"
    assert result["quality"]["coverage"] == "UNKNOWN"
    assert result["quality"]["admitted_to_f"] is False
    assert result["metrics"]["admitted_to_f"] is False
    assert seen[0][0] is store
    assert seen[0][1]["category"] == "inverse"
    assert seen[0][1]["limit"] == 99


@pytest.mark.parametrize("tamper", [
    {"status": "COMPLETE"}, {"source_coverage": "COMPLETE"},
    {"f_admission": "ALLOWED"}, {"quantity_unit": "base"},
    {"method": "tester.execution-metrics/1"},
    {"utc_window": {"start_ms": 0, "end_ms": 120_000}},
])
def test_forged_readback_label_fails_closed(monkeypatch, tamper):
    from src.api import analytics_readback as module
    def source(*args, **kwargs):
        result = {
            "schema_version": "tester.research-ui-observation/1",
            "status": "OBSERVED_UNVERIFIED",
            "f_admission": "DENIED_UNKNOWN_COVERAGE",
            "source_coverage": "UNKNOWN",
            "publication_time": "UNKNOWN",
            "method": "tester.execution-metrics/2",
            "quantity_unit": "quote",
            "source_quantity_unit": "source_native_inverse_contract",
            "utc_window": {"start_ms": 0, "end_ms": 60_000},
            "source_receipts_digest": "b" * 64,
            "observed_sample_fingerprint": "c" * 64,
            "bars": [],
        }
        result.update(tamper)
        return result
    monkeypatch.setattr(module, "read_verified_ui_observation", source)
    assert observed_readback(scope(), trusted_store=type("Store", (), {"read_bounded": lambda self: []})(),
                             trusted_identity=identity())["status"] == "UNVERIFIED"


def test_backend_failure_does_not_leak_sources(monkeypatch):
    from src.api import analytics_readback as module
    def broken(*args, **kwargs):
        raise ValueError("DB secret postgres://private:password@localhost")
    monkeypatch.setattr(module, "read_verified_ui_observation", broken)
    result = observed_readback(scope(), trusted_store=type("Store", (), {"read_bounded": lambda self: []})(), trusted_identity=identity())
    assert result["status"] == "UNVERIFIED"
    assert "secret" not in repr(result)
    assert result["admitted_to_f"] is False

def test_http_query_accepts_canonical_integer_strings_then_refuses_missing_binding():
    app = FastAPI()
    app.include_router(create_readback_router())
    response = TestClient(app).get("/api/analytics/readback", params=scope().model_dump())
    assert response.status_code == 503


def test_valid_scoped_route_can_only_return_unverified_source_observation(monkeypatch):
    from src.api import analytics_readback as module
    class StubStore:
        def read_bounded(self, *args, **kwargs):
            return []
    def canonical(store, **kwargs):
        return {
            "schema_version": "tester.research-ui-observation/1",
            "status": "OBSERVED_UNVERIFIED",
            "f_admission": "DENIED_UNKNOWN_COVERAGE",
            "source_coverage": "UNKNOWN",
            "publication_time": "UNKNOWN",
            "method": "tester.execution-metrics/2",
            "quantity_unit": "quote",
            "source_quantity_unit": "source_native_inverse_contract",
            "utc_window": {"start_ms": 0, "end_ms": 60_000},
            "source_receipts_digest": "b" * 64,
            "observed_sample_fingerprint": "c" * 64,
            "bars": [{
                "start_ms": 0, "end_ms": 60_000,
                "volume": "100", "vwap": "100",
            }],
        }
    monkeypatch.setattr(module, "read_verified_ui_observation", canonical)
    app = FastAPI()
    app.include_router(create_readback_router(
        trusted_binding=lambda: (StubStore(), identity())
    ))
    res = TestClient(app).get("/api/analytics/readback", params=scope().model_dump())
    assert res.status_code == 200
    envelope = res.json()
    assert envelope["schema_version"] == "1.0"
    data = envelope["data"]
    assert data["status"] == "OBSERVED_UNVERIFIED"
    assert data["quality"]["coverage"] == "UNKNOWN"
    assert data["metrics"]["admitted_to_f"] is False
    assert data["admitted_to_f"] is False
    assert data["method"] == "tester.execution-metrics/2"


def test_normal_product_app_has_no_readback_route_or_enabled_capability(tmp_path):
    from src.api.main import create_app
    from src.api.config import Settings
    from src.api.providers import UnavailableReader
    app = create_app(Settings(frontend_dir=tmp_path / "not-installed"),
                     provider=UnavailableReader())
    assert not any(getattr(route, "path", "") == "/api/analytics/readback"
                   for route in app.routes)
    client = TestClient(app, base_url="http://localhost")
    response = client.get("/api/capabilities", params={
        "category": "linear", "symbol": "BTCUSDT", "start_ms": 0, "end_ms": 60_000,
    })
    assert response.status_code == 200
    feature = response.json()["data"]["features"]["analytics_readback"]
    assert feature["enabled"] is False
    assert feature["resource"] is None
    assert feature["status"] == "NOT_REGISTERED"


def test_duplicate_query_pollution_is_rejected_before_binding():
    from src.api import analytics_readback as module
    app = FastAPI()
    app.include_router(create_readback_router(
        trusted_binding=lambda: (_ for _ in ()).throw(
            AssertionError("No binding should be called")
        )))
    good = list(scope().model_dump().items())
    repeated = good + [("category", "linear")]
    result = TestClient(app).get("/api/analytics/readback", params=repeated)
    assert result.status_code == 422
    assert result.json()["detail"] == "invalid_readback_scope"


def test_concurrent_capacity_refuses_without_touching_storage(monkeypatch):
    from src.api import analytics_readback as module

    class Exhausted:
        def acquire(self, blocking=False):
            assert blocking is False
            return False
        def release(self):
            raise AssertionError("Never acquired")

    monkeypatch.setattr(module, "BoundedSemaphore", lambda count: Exhausted())
    app = FastAPI()
    app.include_router(create_readback_router(
        trusted_binding=lambda: (_ for _ in ()).throw(
            AssertionError("No binding should be called")
        )))
    result = TestClient(app).get("/api/analytics/readback", params=scope().model_dump())
    assert result.status_code == 429
    assert result.json()["detail"] == "analytics_readback_concurrency_limit"


def test_readback_wall_deadline_is_fail_closed(monkeypatch):
    from src.api import analytics_readback as module

    class Store:
        def read_bounded(self, *args, **kwargs):
            return []
    monkeypatch.setattr(module.time, "monotonic", iter([0., 11.]).__next__)

    def observed(store, **kwargs):
        return dict(
            schema_version="tester.research-ui-observation/1",
            status="OBSERVED_UNVERIFIED",
            f_admission="DENIED_UNKNOWN_COVERAGE",
            source_coverage="UNKNOWN", publication_time="UNKNOWN",
            method="tester.execution-metrics/2", quantity_unit="quote",
            source_quantity_unit="source_native_inverse_contract",
            utc_window={"start_ms": 0, "end_ms": 60_000},
            source_receipts_digest="b" * 64,
            observed_sample_fingerprint="c" * 64,
            bars=[{"start_ms": 0, "end_ms": 60_000, "volume": "100", "vwap": "100"}],
        )
    monkeypatch.setattr(module, "read_verified_ui_observation", observed)
    app = FastAPI()
    app.include_router(create_readback_router(
        trusted_binding=lambda: (Store(), identity())
    ))
    response = TestClient(app).get("/api/analytics/readback", params=scope().model_dump())
    assert response.status_code == 503
    assert response.json()["detail"] == "analytics_readback_timeout"
