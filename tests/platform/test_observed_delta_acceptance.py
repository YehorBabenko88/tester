from src.api.catalog_history import qualify_delta

HOUR = 3600000


def metric(
    value,
    timestamp,
    *,
    unit="USDT",
    source="https://api.bybit.com/v5/market/tickers?category=linear",
):
    return {
        "value": str(value),
        "state": "OBSERVED",
        "unit": unit,
        "source": source,
        "source_time_ms": timestamp,
        "observed_at": timestamp / 1000,
    }


def test_one_hour_price_change_from_real_baseline():
    old = metric("100", 1790000000000)
    new = metric("110", 1790000000000 + HOUR)
    result = qualify_delta(new, old, HOUR)
    assert result["state"] == "OBSERVED"
    assert result["value"] == "0.1"
    assert result["baseline_source_time_ms"] == old["source_time_ms"]


def test_one_hour_oi_change_from_real_baseline():
    old = metric("500", 1790000000000, unit="BTC")
    new = metric("450", 1790000000000 + HOUR, unit="BTC")
    result = qualify_delta(new, old, HOUR)
    assert result["state"] == "OBSERVED"
    assert result["value"] == "-0.1"


def test_24h_requires_actual_24h_observation():
    old = metric("500", 1790000000000, unit="BTC")
    new = metric("550", 1790000000000 + HOUR, unit="BTC")
    result = qualify_delta(new, old, 24 * HOUR)
    assert result["state"] == "UNAVAILABLE"
    assert result["reason"] == "history_window_unqualified"


def test_missing_baseline_never_becomes_zero():
    current = metric("100", 1790000000000)
    result = qualify_delta(current, None, HOUR)
    assert result["state"] == "UNAVAILABLE"
    assert result["value"] is None


def test_different_units_and_source_are_rejected():
    old = metric("100", 1790000000000, unit="BTC")
    new = metric("110", 1790000000000 + HOUR, unit="USDT")
    assert qualify_delta(new, old, HOUR)["reason"] == "history_unit_mismatch"
    new = metric(
        "110", 1790000000000 + HOUR, unit="BTC", source="https://example.com/other"
    )
    assert qualify_delta(new, old, HOUR)["reason"] == "history_source_mismatch"
