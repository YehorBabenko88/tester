"""Pure source-boundary regressions: no HTTP, database or runtime startup."""

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
                result["list"] = [dict(symbol="BTCUSDT", status="Trading",
                                       contractType="LinearPerpetual", baseCoin="BTC",
                                       quoteCoin="USDT", settleCoin="USDT")]
        return result, "https://api.bybit.com/v5/market" + endpoint


UNSIGNED = [("lastPrice", "price"), ("volume24h", "volume_24h"),
            ("turnover24h", "turnover_24h"), ("openInterest", "open_interest")]


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
