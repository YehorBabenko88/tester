"""Deterministic arithmetic and fail-closed research metrics."""

from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from src.analytics.core import normalize_trade
from src.analytics.research_metrics import (
    METHOD_V1,
    METHOD_V2,
    calculate,
    calculate_v2,
    stored_metrics_status,
)
from src.strategy.dsl import digest


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
    result = calculate_v2(rows, start_ms=0, end_ms=60000, interval_ms=60000, tick_size="1")
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
    result = calculate_v2(rows, start_ms=0, end_ms=60000, interval_ms=60000, tick_size="1")
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
    metrics = calculate_v2(trades, start_ms=0, end_ms=60000, interval_ms=60000, tick_size="1")
    bar = metrics["bars"][0]
    (stats,) = candle_statistics(trades, interval="1", price_step="1")
    assert metrics["quantity_unit"] == stats["quantity_unit"]
    assert abs(exact(bar["vwap"]) - exact(stats["vwap"])) < Fraction(1, 10**40)
    assert exact(bar["volume"]) == exact(stats["buy_volume"]) + exact(stats["sell_volume"]) + exact(
        stats["unknown_side_volume"]
    )
    assert bar["poc"] == stats["point_of_control"]


# Pre-PR115 (main a1d38d0, method /1) digests of the same mixed inputs.
GOLDEN_V1 = {
    "linear": "1f77b7317a772e0e0978e9c4de14797f639c29e4f2f9a7d7cc714f0cb2ee0ec1",
    "spot": "af613811fb91bb3525e023a3ea9789d5970a130ca735573a43f4da279e9231d9",
    # Original /1 inverse output (arithmetic VWAP, wrong); must never be produced again.
    "inverse": "28ddaeae79242543a035043fd79e8c9d5ccc41a80315d2eeee3c1bab3128cba9",
}
GOLDEN_V1_EMPTY = "2784f14aae0c3cdee8af91e5dc4926b4f53374726bfe8309300469851b89ed0c"
GOLDEN_V2 = {
    "linear": "825525a3a782cef6cb224f5c52cefe4218c88b4467650c68ee9c45ef416e6ade",
    "spot": "0af6dda9e4e4ba9ba42b2d2baa01acd457c22052ee9d7f97f6f34a296ac22d81",
    "inverse": "77e25979ad4c77b71b98669151382a064ee2fad7557a8f972dba722bfb3b67f2",
}
SYMBOLS = {"linear": "BTCUSDT", "spot": "BTCUSDT", "inverse": "BTCUSD"}
WINDOW = dict(start_ms=0, end_ms=180000, interval_ms=60000, tick_size="0.5")


def mixed(category):
    symbol = SYMBOLS[category]
    return [
        market_trade(category, symbol, 1, "100", "100", "Buy", "g1"),
        market_trade(category, symbol, 2, "200", "100", "Sell", "g2"),
        market_trade(category, symbol, 3, "150.5", "7", "Unknown", "g3"),
        market_trade(category, symbol, 60001, "101", "3", "Buy", "g4"),
        market_trade(category, symbol, 60002, "99", "0.25", "Unknown", "g5"),
    ]


@pytest.mark.parametrize("category", ["linear", "spot"])
def test_v1_linear_spot_output_is_byte_identical_to_pre_pr115(category):
    result = calculate(mixed(category), **WINDOW)
    assert result["method"] == METHOD_V1
    assert "quantity_unit" not in result
    assert digest(result) == GOLDEN_V1[category]
    assert stored_metrics_status(result) == {"method": METHOD_V1, "quantity_unit": "base"}
    assert digest(calculate([], **WINDOW | {"end_ms": 60000, "tick_size": "1"})) == (
        GOLDEN_V1_EMPTY
    )


def test_v1_refuses_inverse_instead_of_reinterpreting():
    with pytest.raises(ValueError, match="inverse_requires_execution_metrics_v2"):
        calculate(mixed("inverse"), **WINDOW)
    # A stored /1 inverse result (original wrong VWAP) is rejected, not upgraded.
    stored = calculate_v2(mixed("inverse"), **WINDOW)
    stored["method"] = METHOD_V1
    del stored["quantity_unit"]
    with pytest.raises(ValueError, match="v1_inverse_vwap_invalid"):
        stored_metrics_status(stored)
    assert digest(stored) != GOLDEN_V1["inverse"]


@pytest.mark.parametrize("category", ["linear", "spot", "inverse"])
def test_v2_is_versioned_deterministic_and_never_admits_f(category):
    first = calculate_v2(mixed(category), **WINDOW)
    second = calculate_v2(list(reversed(mixed(category))), **WINDOW)
    assert first == second
    assert first["method"] == METHOD_V2
    assert digest(first) == GOLDEN_V2[category]
    assert digest(first) != GOLDEN_V1[category]
    assert first["admitted_to_f"] is False
    assert first["quantity_unit"] == ("quote" if category == "inverse" else "base")
    assert stored_metrics_status(first)["method"] == METHOD_V2
    claimed = dict(first, admitted_to_f=True)
    with pytest.raises(ValueError, match="claims_f_admission"):
        stored_metrics_status(claimed)
    for wrong in ({**first, "method": "tester.execution-metrics/3"}, None):
        with pytest.raises(ValueError, match="unknown_execution_metrics_method"):
            stored_metrics_status(wrong)


def test_v2_inverse_mixed_unknown_side_exact_issue112():
    bar0, bar1, bar2 = calculate_v2(mixed("inverse"), **WINDOW)["bars"]
    # quote 207 / base (1 + 1/2 + 7/150.5) = 17802/133
    assert abs(exact(bar0["vwap"]) - Fraction(17802, 133)) < Fraction(1, 10**46)
    assert bar0["volume"] == "207"
    assert bar0["unknown_side_volume"] == "7"
    assert bar0["delta_known"] == "0"
    assert bar0["side_classification_complete"] is False
    assert "aggressor_unknown" in bar0["reasons"]
    quote1, base1 = Fraction(13, 4), Fraction(3, 101) + Fraction(1, 4) / 99
    assert abs(exact(bar1["vwap"]) - quote1 / base1) < Fraction(1, 10**46)
    assert bar1["cvd_known"] == "3"
    assert bar2["vwap"] is None and bar2["trade_count"] == 0


def test_mixed_categories_rejected_in_both_versions():
    rows = [mixed("linear")[0], market_trade("inverse", "BTCUSD", 4, "100", "1", "Buy", "z")]
    for fn in (calculate, calculate_v2):
        with pytest.raises(ValueError, match="mixed market identities"):
            fn(rows, **WINDOW)


def test_unknown_method_refused():
    with pytest.raises(ValueError, match="unknown_execution_metrics_method"):
        calculate([], **WINDOW, method="tester.execution-metrics/0")
