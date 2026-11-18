from src.api.observed_volatility import realized_volatility

STEP = 60_000
BASE = 1_790_000_000_000


def candles(values):
    return [
        {"timestamp_ms": BASE + i * STEP, "close": str(v),
         "closed": True, "source": "https://api.bybit.com/v5/market/kline?category=linear"}
        for i, v in enumerate(values)
    ]


def test_constant_price_zero_volatility():
    result = realized_volatility(candles([100, 100, 100, 100]), interval_ms=STEP)
    assert result["state"] == "OBSERVED"
    assert result["value"] == "0"
    assert result["sample_count"] == 3


def test_variable_price_nonzero_and_annualized():
    rows = candles([100, 110, 90, 100])
    raw = realized_volatility(rows, interval_ms=STEP)
    annual = realized_volatility(rows, interval_ms=STEP, annualize=True)
    assert raw["state"] == annual["state"] == "OBSERVED"
    assert float(raw["value"]) > 0
    assert float(annual["value"]) > float(raw["value"])


def test_rejects_missing_or_unverified_source():
    rows = candles([100, 101, 102])
    rows[1]["source"] = ""
    assert realized_volatility(rows, interval_ms=STEP)["reason"] == "missing_candle_provenance"
    rows = candles([100, 101, 102])
    rows[1]["closed"] = False
    assert realized_volatility(rows, interval_ms=STEP)["reason"] == "unverified_closed_candle"


def test_rejects_gap_and_reorder():
    rows = candles([100, 101, 102])
    rows[1]["timestamp_ms"] += 1
    assert realized_volatility(rows, interval_ms=STEP)["reason"] == "candle_window_gap_or_order"
    rows = candles([100, 101, 102])
    rows.reverse()
    assert realized_volatility(rows, interval_ms=STEP)["reason"] == "candle_window_gap_or_order"


def test_rejects_nonpositive_or_nan_close():
    for value in ("0", "-1", "NaN", "Infinity", "not-a-number"):
        rows = candles([100, 101, value])
        assert realized_volatility(rows, interval_ms=STEP)["reason"] == "invalid_close"


def test_rejects_insufficient_window():
    assert realized_volatility(candles([100, 101]), interval_ms=STEP)["reason"] == "insufficient_closed_candles"
