"""Inverse/linear/spot unit conventions across the canonical analytics math.

One quantity-unit table (src.analytics.core.QUANTITY_UNITS) feeds the /2
metrics; the canonical footprint, taker delta, trade notional, candle
statistics and research metrics must agree exactly per category, and no
canonical aggregation may mix two markets (two units).
"""

from fractions import Fraction

import pytest

from src.analytics.core import (
    QUANTITY_UNITS,
    footprint,
    normalize_trade,
    price_levels,
    quantity_unit,
    trade_notional,
)
from src.analytics.exact import decimal, number
from src.analytics.research_metrics import calculate_v2
from src.analytics.statistics import candle_statistics

SYMBOLS = {"linear": "BTCUSDT", "spot": "BTCUSDT", "inverse": "BTCUSD"}


def trade(category, ts, price, qty, side, trade_id, symbol=None):
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
        symbol=symbol or SYMBOLS[category],
        source_ref=source,
    )


def sample(category):
    # 100 @ 100 taker Buy + 100 @ 200 taker Sell (source-native quantity).
    return [
        trade(category, 1, "100", "100", "Buy", "u1"),
        trade(category, 2, "200", "100", "Sell", "u2"),
    ]


EXPECTED_VWAP = {
    # inverse: sum(q)/sum(q/p) = 200 / (1 + 1/2)
    "inverse": Fraction(400, 3),
    # linear/spot: sum(p*q)/sum(q) = 30000 / 200
    "linear": Fraction(150),
    "spot": Fraction(150),
}


def test_quantity_unit_table_is_the_single_category_mapping():
    assert QUANTITY_UNITS == {"spot": "base", "linear": "base", "inverse": "quote"}
    for category, unit in QUANTITY_UNITS.items():
        assert quantity_unit(category) == unit
    with pytest.raises(ValueError):
        quantity_unit("option")


@pytest.mark.parametrize("category", ["inverse", "linear", "spot"])
def test_vwap_footprint_delta_cvd_exact_and_consistent_per_category(category):
    trades = sample(category)
    metrics = calculate_v2(trades, start_ms=0, end_ms=60000, interval_ms=60000, tick_size="1")
    assert metrics["quantity_unit"] == quantity_unit(category)
    (bar,) = metrics["bars"]
    # Exact rational serialized by the canonical exact.decimal (50 digits for
    # the repeating inverse ratio 400/3; exact "150" for linear/spot).
    assert bar["vwap"] == decimal(EXPECTED_VWAP[category])
    # Footprint/delta/CVD: source-native unit, per side, never converted.
    assert (bar["volume"], bar["buy_volume"], bar["sell_volume"]) == ("200", "100", "100")
    assert (bar["delta_known"], bar["cvd_known"]) == ("0", "0")
    assert [(lv["price"], lv["buy_volume"], lv["sell_volume"]) for lv in bar["footprint"]] == [
        ("100", "100", "0"),
        ("200", "0", "100"),
    ]
    # Canonical v1 footprint and price-level clusters agree in the same unit.
    (record,) = footprint(trades, interval="1", price_step="1")
    assert (record["buy_volume"], record["sell_volume"], record["delta"]) == ("100", "100", "0")
    levels = price_levels(trades, interval="1", price_step="1")
    assert [
        (lv["price_lower"], lv["footprint"]["buy_volume"], lv["footprint"]["sell_volume"])
        for lv in levels
    ] == [("100", "100", "0"), ("200", "0", "100")]
    # trade_notional: same VWAP from quote notional / base quantity.
    notionals = [trade_notional(t) for t in trades]
    quote = sum((number(n["value"]) for n in notionals), Fraction())
    base = sum((number(n["base_quantity"]) for n in notionals), Fraction())
    assert quote / base == EXPECTED_VWAP[category]
    # candle_statistics reports the same unit and VWAP.
    (stats,) = candle_statistics(trades, interval="1", price_step="1")
    assert stats["quantity_unit"] == metrics["quantity_unit"]
    assert number(stats["base_volume"]) == base and number(stats["quote_notional"]) == quote
    if category == "inverse":
        # base quantity exists only as the VWAP denominator: 100/100 + 100/200.
        assert base == Fraction(3, 2) and quote == 200
    else:
        assert base == 200 and quote == 30000


def test_inverse_vwap_exact_fraction_serialization():
    (bar,) = calculate_v2(
        sample("inverse"), start_ms=0, end_ms=60000, interval_ms=60000, tick_size="1"
    )["bars"]
    assert bar["vwap"] == decimal(Fraction(400, 3))
    assert abs(Fraction(bar["vwap"]) - Fraction(400, 3)) < Fraction(1, 10**46)


def test_mixed_unit_aggregation_is_refused_or_kept_separate():
    linear = trade("linear", 1, "100", "1", "Buy", "m1", symbol="BTCUSDT")
    inverse = trade("inverse", 2, "100", "100", "Buy", "m2", symbol="BTCUSD")
    with pytest.raises(ValueError, match="mixed market identities"):
        calculate_v2([linear, inverse], start_ms=0, end_ms=60000, interval_ms=60000, tick_size="1")
    # Same symbol name in two categories is still two units.
    same_name = trade("inverse", 2, "100", "100", "Buy", "m3", symbol="BTCUSDT")
    with pytest.raises(ValueError, match="mixed market identities"):
        calculate_v2(
            [linear, same_name], start_ms=0, end_ms=60000, interval_ms=60000, tick_size="1"
        )
    # Canonical footprint/statistics group per market: never summed together.
    records = footprint([linear, inverse], interval="1", price_step="1")
    assert sorted((r["category"], r["buy_volume"]) for r in records) == [
        ("inverse", "100"),
        ("linear", "1"),
    ]
    stats = candle_statistics([linear, inverse], interval="1", price_step="1")
    assert sorted((s["market"]["category"], s["quantity_unit"]) for s in stats) == [
        ("inverse", "quote"),
        ("linear", "base"),
    ]
