"""Deterministic arithmetic and fail-closed research metrics."""

from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from src.analytics.core import normalize_trade
from src.analytics.research_metrics import calculate


def trade(ts, price, qty, side, trade_id):
    source = {
        "source_kind": "bybit_rest",
        "source_ref": "fixture-rest",
        "source_hash": None,
        "ingestion_run": "test-run",
        "timestamp_precision": "ms",
        "source_timestamp": str(ts),
    }
    return normalize_trade(
        {"time": str(ts), "price": price, "size": qty, "side": side, "execId": trade_id},
        category="linear",
        symbol="BTCUSDT",
        source_ref=source,
    )


def run(rows):
    return calculate(rows, start_ms=0, end_ms=120000, interval_ms=60000, tick_size="0.5")


def test_exact_vwap_delta_cvd_and_poc_tie():
    a = run(
        [
            trade(1, "100", "0.1", "Buy", "a"),
            trade(2, "101", "0.2", "Sell", "b"),
            trade(60001, "100", "0.3", "Buy", "c"),
        ]
    )
    x, y = a["bars"]
    with localcontext() as context:
        context.prec = 50
        assert Decimal(x["vwap"]) == Decimal(302) / Decimal(3)
    assert len(Decimal(x["vwap"]).as_tuple().digits) == 50
    # Compare independently to the exact rational rather than counting digits.
    assert abs(Fraction(Decimal(x["vwap"])) - Fraction(302, 3)) < Fraction(1, 10**46)
    assert x["delta_known"] == "-0.1"
    assert x["cvd_known"] == "-0.1"
    assert x["poc"] == "101"
    assert y["cvd_known"] == "0.2"
    assert y["poc"] == "100"
    assert a["admitted_to_f"] is False


def test_unknown_side_not_assumed_bid_ask():
    row = trade(1, "100", "2", "Unknown", "u")
    b = run([row])["bars"][0]
    assert b["volume"] == "2"
    assert b["delta_known"] == "0"
    assert b["unknown_side_volume"] == "2"
    assert b["delta_complete"] is False
    assert b["footprint"][0]["unknown_side_volume"] == "2"


def test_deduplicate_and_reject_conflicting_identity():
    a = trade(1, "100", "1", "Buy", "a")
    assert run([a, a])["bars"][0]["volume"] == "1"
    b = trade(1, "101", "1", "Buy", "a")
    with pytest.raises(ValueError):
        run([a, b])


def test_empty_interval_does_not_fabricate_vwap():
    a = run([trade(1, "100", "1", "Buy", "a")])
    assert a["bars"][1]["vwap"] is None
    assert a["bars"][1]["poc"] is None
    assert "empty_interval" in a["bars"][1]["reasons"]


def test_bounds_and_float_rejected():
    a = trade(1, "100", "1", "Buy", "a")
    with pytest.raises(ValueError):
        run([trade(120000, "100", "1", "Buy", "b")])
    with pytest.raises(ValueError):
        calculate([a], start_ms=0, end_ms=120000, interval_ms=60000, tick_size=0.5)


def test_poc_lower_price_on_equal_volume():
    x = run([trade(1, "101", "1", "Buy", "a"), trade(2, "100", "1", "Sell", "b")])["bars"][0]
    assert x["poc"] == "100"


def test_empty_dataset_refuses_admission():
    a = run([])
    assert a["status"] == "NO_DATA"
    assert a["admitted_to_f"] is False


def test_known_side_sample_does_not_certify_complete_delta():
    bar = run([trade(1, "100", "1", "Buy", "a")])["bars"][0]
    assert bar["side_classification_complete"] is True
    assert bar["delta_complete"] is False
    assert bar["coverage_certified"] is False


def test_bounded_bar_window():
    with pytest.raises(ValueError, match="bar limit exceeded"):
        calculate([], start_ms=0, end_ms=1001 * 60000, interval_ms=60000, tick_size="0.5")


def test_unverified_source_hash_is_disclosed():
    bar = run([trade(1, "100", "1", "Buy", "a")])["bars"][0]
    assert "source_content_hash_missing" in bar["reasons"]
    assert bar["vwap_volume_coverage_certified"] is False
    assert bar["poc_volume_coverage_certified"] is False


def test_observed_sample_counts_are_not_completeness_proofs():
    a = run([trade(1, "100", "1", "Unknown", "a")])
    assert a["observed_execution_count"] == 1
    assert a["empty_bar_count"] == 1
    assert a["unknown_side_bar_count"] == 1
    assert a["admitted_to_f"] is False


def test_cvd_is_window_anchored_not_full_market_history():
    result = run([trade(1, "100", "1", "Buy", "a")])
    assert result["cvd_anchor_start_ms"] == 0
    assert result["cvd_prior_history_known"] is False
    assert result["bars"][0]["cvd_known"] == "1"
    assert result["admitted_to_f"] is False


def test_repeating_one_third_decimal_uses_fifty_significant_digits():
    rows = [
        trade(1, "1", "2", "Buy", "one-third-a"),
        trade(2, "2", "1", "Sell", "one-third-b"),
    ]
    bar = run(rows)["bars"][0]
    assert bar["delta_known"] == "1"
    assert bar["volume"] == "3"
    assert bar["poc"] == "1"
    with localcontext() as context:
        context.prec = 50
        assert Decimal(bar["vwap"]) == Decimal(4) / Decimal(3)
    assert len(Decimal(bar["vwap"]).as_tuple().digits) == 50
    assert abs(Fraction(Decimal(bar["vwap"])) - Fraction(4, 3)) < Fraction(1, 10**48)


def test_poc_tie_break_is_lower_price_independent_of_order():
    a = trade(2, "101", "1", "Sell", "tie-b")
    b = trade(1, "100", "1", "Buy", "tie-a")
    for rows in ([a, b], [b, a]):
        bar = run(rows)["bars"][0]
        assert bar["poc"] == "100"
        assert bar["delta_known"] == "0"
        assert bar["cvd_known"] == "0"
        assert bar["vwap"] == "100.5"
        assert [level["price"] for level in bar["footprint"]] == ["100", "101"]


def market_trade(category, symbol, ts, price, qty, side, trade_id):
    source = {
        "source_kind": "bybit_rest",
        "source_ref": "fixture-rest",
        "source_hash": None,
        "ingestion_run": "test-run",
        "timestamp_precision": "ms",
        "source_timestamp": str(ts),
    }
    return normalize_trade(
        {"time": str(ts), "price": price, "size": qty, "side": side, "execId": trade_id},
        category=category,
        symbol=symbol,
        source_ref=source,
    )


def exact(value):
    return Fraction(Decimal(value))


def test_inverse_vwap_uses_quote_quantity_issue112():
    # Inverse size is quote-denominated (USD contracts): VWAP = sum(q) / sum(q/p).
    # Before the fix this returned sum(p*q)/sum(q) = 150.
    rows = [
        market_trade("inverse", "BTCUSD", 1, "100", "100", "Buy", "i1"),
        market_trade("inverse", "BTCUSD", 2, "200", "100", "Buy", "i2"),
    ]
    result = calculate(rows, start_ms=0, end_ms=60000, interval_ms=60000, tick_size="1")
    bar = result["bars"][0]
    assert abs(exact(bar["vwap"]) - Fraction(400, 3)) < Fraction(1, 10**46)
    assert bar["vwap"].startswith("133.3333")
    assert result["quantity_unit"] == "quote"
    # Volume, delta and POC stay in the venue-native (quote) execution unit.
    assert bar["volume"] == "200"
    assert bar["delta_known"] == "200"
    assert bar["poc"] == "100"


@pytest.mark.parametrize("category,symbol", [("linear", "BTCUSDT"), ("spot", "BTCUSDT")])
def test_linear_and_spot_vwap_use_base_quantity_issue112(category, symbol):
    rows = [
        market_trade(category, symbol, 1, "100", "1", "Buy", "b1"),
        market_trade(category, symbol, 2, "200", "3", "Sell", "b2"),
    ]
    result = calculate(rows, start_ms=0, end_ms=60000, interval_ms=60000, tick_size="1")
    bar = result["bars"][0]
    assert result["quantity_unit"] == "base"
    assert exact(bar["vwap"]) == Fraction(700, 4)
    assert bar["volume"] == "4"
    assert bar["poc"] == "200"


@pytest.mark.parametrize(
    "category,symbol,rows",
    [
        ("inverse", "BTCUSD", [("100", "100", "Buy"), ("200", "100", "Sell"), ("150", "7", "Buy")]),
        ("linear", "BTCUSDT", [("100", "0.1", "Buy"), ("101", "0.2", "Sell"), ("99", "3", "Buy")]),
        ("spot", "ETHUSDT", [("3000", "1.5", "Sell"), ("3001", "0.25", "Buy")]),
    ],
)
def test_vwap_volume_and_poc_match_candle_statistics_issue112(category, symbol, rows):
    from src.analytics.statistics import candle_statistics

    trades = [
        market_trade(category, symbol, 1 + i, p, q, s, f"x{i}") for i, (p, q, s) in enumerate(rows)
    ]
    metrics = calculate(trades, start_ms=0, end_ms=60000, interval_ms=60000, tick_size="1")
    bar = metrics["bars"][0]
    (stats,) = candle_statistics(trades, interval="1", price_step="1")
    assert metrics["quantity_unit"] == stats["quantity_unit"]
    assert abs(exact(bar["vwap"]) - exact(stats["vwap"])) < Fraction(1, 10**40)
    assert exact(bar["volume"]) == exact(stats["buy_volume"]) + exact(stats["sell_volume"]) + exact(
        stats["unknown_side_volume"]
    )
    assert bar["poc"] == stats["point_of_control"]
