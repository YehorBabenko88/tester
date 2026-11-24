"""Pure source-boundary regressions: no HTTP, database or runtime startup."""

from copy import deepcopy

import pytest

from src.api.market_catalog import CatalogService


class Source:
    def __init__(self, field, value):
        self.field, self.value = field, value

    def call(self, endpoint, params):
        category = params["category"]
        result = {"category": category, "_source_time_ms": 1_000_000}
        if endpoint == "/tickers":
            result["list"] = [{"symbol": "BTCUSDT", self.field: self.value}]
        else:
            result.update(nextPageCursor="", list=[])
            if params["status"] == "Trading":
                result["list"] = [
                    dict(
                        symbol="BTCUSDT",
                        status="Trading",
                        contractType="LinearPerpetual",
                        baseCoin="BTC",
                        quoteCoin="USDT",
                        settleCoin="USDT",
                    )
                ]
        return result, "https://api.bybit.com/v5/market" + endpoint


UNSIGNED = [
    ("lastPrice", "price"),
    ("volume24h", "volume_24h"),
    ("turnover24h", "turnover_24h"),
    ("openInterest", "open_interest"),
]


def metric(field, value, name):
    service = CatalogService(Source(field, value), clock=lambda: 1000)
    assert service.refresh("linear")
    return service.query("linear")["rows"][0]["metrics"][name]


@pytest.mark.parametrize("field,name", UNSIGNED)
def test_negative_unsigned_metric_is_unavailable_with_original_evidence(field, name):
    result = metric(field, "-1.25", name)
    assert result["state"] == "UNAVAILABLE"
    assert result["value"] is None
    assert result["reason"] == "negative_value"
    assert result["raw_value"] == "-1.25"
    assert result["source"] == "https://api.bybit.com/v5/market/tickers"
    assert result["source_time_ms"] == 1_000_000
    assert result["observed_at"] == 1000


@pytest.mark.parametrize("field,name", UNSIGNED)
@pytest.mark.parametrize("value", ["0", "-0", "12.5"])
def test_existing_zero_and_positive_unsigned_semantics_remain(field, name, value):
    result = metric(field, value, name)
    assert result["state"] == "OBSERVED"
    assert result["value"] == value
    assert result["reason"] is None


@pytest.mark.parametrize("value", ["-0.25", "0", "0.25"])
def test_price_change_remains_signed(value):
    result = metric("price24hPcnt", value, "price_change_24h")
    assert result["state"] == "OBSERVED"
    assert result["value"] == value
    assert result["unit"] == "ratio"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "invalid"])
def test_nonfinite_or_malformed_values_remain_unavailable(value):
    result = metric("lastPrice", value, "price")
    assert result["state"] == "UNAVAILABLE"
    assert result["value"] is None


@pytest.mark.parametrize("field,name", UNSIGNED)
@pytest.mark.parametrize("ticker_failure", [False, True])
def test_legacy_persisted_negative_projection_preserves_stored_fact(field, name, ticker_failure):
    from src.api.persistent_market_catalog import PersistentCatalogService
    from tests.platform.test_catalog_runtime import MemoryStore

    source = Source(field, "1.25")
    old = CatalogService(source, clock=lambda: 1000)
    assert old.refresh("linear")
    snapshot = deepcopy(old._snapshots["linear"])
    snapshot["rows"][0]["metrics"][name]["value"] = "-1.25"
    store = MemoryStore()
    store.state["linear"].update(snapshot=snapshot, attempt=deepcopy(old._attempts["linear"]))
    service = PersistentCatalogService(source, store, clock=lambda: 1000, jitter=lambda: 0)
    if ticker_failure:
        original = source.call

        def fail_tickers(endpoint, params):
            if endpoint == "/tickers":
                raise OSError("offline")
            return original(endpoint, params)

        source.call = fail_tickers
        assert not service.refresh("linear")
    stored_before = deepcopy(store.state["linear"])
    result = service.query("linear")["rows"][0]["metrics"][name]
    assert result["state"] == "UNAVAILABLE"
    assert result["value"] is None
    assert result["reason"] == "negative_value"
    assert result["raw_value"] == "-1.25"
    assert result["source_time_ms"] == 1_000_000
    assert result["observed_at"] == 1000
    assert store.state["linear"] == stored_before
    stored_metric = stored_before["snapshot"]["rows"][0]["metrics"][name]
    assert stored_metric["value"] == "-1.25"
    assert stored_metric["state"] == ("STALE" if ticker_failure else "OBSERVED")
