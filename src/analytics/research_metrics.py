"""Execution-derived research metrics; never infer aggressor or historical coverage.

Pure bounded calculations. This is NOT a real-data F admission authority.
"""
from collections import defaultdict
from fractions import Fraction

from src.analytics.core import deduplicate, validate_trade
from src.analytics.exact import decimal, number

# Output contract versions.  /1 is frozen: linear/spot outputs (and digests)
# are byte-identical to the original release; /1 never had a correct inverse
# VWAP, so /1 now refuses inverse executions instead of reinterpreting them.
# /2 adds ``quantity_unit`` and the quote/base VWAP for every category.
METHOD_V1 = "tester.execution-metrics/1"
METHOD_V2 = "tester.execution-metrics/2"
METHOD = METHOD_V1  # default of calculate(); existing /1 consumers stay unchanged
METHODS = (METHOD_V1, METHOD_V2)
MAX_TRADES = 100_000
MAX_BARS = 1000


def calculate_v2(trades, *, start_ms: int, end_ms: int, interval_ms: int, tick_size: str):
    """``calculate`` with the category-aware /2 output contract."""
    return calculate(trades, start_ms=start_ms, end_ms=end_ms, interval_ms=interval_ms,
                     tick_size=tick_size, method=METHOD_V2)


def stored_metrics_status(metrics):
    """Classify a persisted metrics result without recomputing or upgrading it.

    /1 results are never reinterpreted as /2.  A /1 result for an inverse
    market was produced by the original arithmetic (base-weighted) VWAP and is
    rejected; /1 linear/spot results remain valid as /1 (base quantity units).
    """
    if not isinstance(metrics, dict) or metrics.get("method") not in METHODS:
        raise ValueError("unknown_execution_metrics_method")
    if metrics.get("admitted_to_f") is not False:
        raise ValueError("execution_metrics_claims_f_admission")
    method = metrics["method"]
    market = metrics.get("market")
    category = market.get("category") if isinstance(market, dict) else None
    if method == METHOD_V1:
        if "quantity_unit" in metrics:
            raise ValueError("v1_metrics_with_v2_fields")
        if category == "inverse":
            raise ValueError("v1_inverse_vwap_invalid_recompute_as_v2")
        return {"method": method, "quantity_unit": "base" if category else None}
    if category is not None and metrics.get("quantity_unit") != (
            "quote" if category == "inverse" else "base"):
        raise ValueError("v2_quantity_unit_mismatch")
    return {"method": method, "quantity_unit": metrics.get("quantity_unit")}


def calculate(trades, *, start_ms: int, end_ms: int, interval_ms: int, tick_size: str,
              method: str = METHOD_V1):
    """Return deterministic footprint, delta, CVD, VWAP and volume POC.

    A taker Buy is positive delta; Sell negative. Unknown taker direction
    contributes to volume/VWAP/POC but NOT delta. No Bid/Ask reconstruction.
    Coverage/availability is reported separately and never promoted.

    Volume, buy/sell/unknown volume, delta, CVD, footprint levels and POC are
    in the venue-native execution quantity unit reported as ``quantity_unit``:
    base asset for linear and spot, quote currency (contracts) for inverse.
    VWAP is always quote per base: sum(quote notional) / sum(base quantity),
    i.e. sum(p*q)/sum(q) for linear/spot and sum(q)/sum(q/p) for inverse
    (same convention as ``statistics.candle_statistics``).

    ``method`` selects the output contract: METHOD_V1 (default, frozen
    linear/spot output without ``quantity_unit``; inverse refused) or
    METHOD_V2 (all categories, ``quantity_unit`` reported).
    """
    if method not in METHODS:
        raise ValueError("unknown_execution_metrics_method")
    if any(type(x) is not int for x in (start_ms, end_ms, interval_ms)):
        raise ValueError("integer millisecond bounds required")
    if start_ms < 0 or end_ms <= start_ms or interval_ms <= 0:
        raise ValueError("invalid bounds")
    if (end_ms - start_ms) % interval_ms:
        raise ValueError("unaligned observation window")
    if (end_ms - start_ms) // interval_ms > MAX_BARS:
        raise ValueError("bar limit exceeded")
    step = number(tick_size, positive=True)
    if step.denominator > 10**18 or step.numerator > 10**18:
        raise ValueError("tick size outside bounded precision")
    if not isinstance(trades, list):
        raise ValueError("canonical_execution_list_required")
    if len(trades) > MAX_TRADES:
        raise ValueError("execution limit exceeded")
    canonical = deduplicate([validate_trade(t) for t in trades])
    if not canonical:
        return {"method": method, "status": "NO_DATA", "admitted_to_f": False,
                "bars": [], "reasons": ["no_executions"]}
    scopes = {(t["venue"], t["category"], t["symbol"]) for t in canonical}
    if len(scopes) != 1:
        raise ValueError("mixed market identities")
    inverse = next(iter(scopes))[1] == "inverse"
    if inverse and method == METHOD_V1:
        raise ValueError("inverse_requires_execution_metrics_v2")
    buckets = defaultdict(list)
    for t in canonical:
        ts = t["timestamp_ms"]
        if not start_ms <= ts < end_ms:
            raise ValueError("execution outside requested window")
        bucket = start_ms + (ts - start_ms) // interval_ms * interval_ms
        buckets[bucket].append(t)
    running = Fraction()
    bars = []
    for start in range(start_ms, end_ms, interval_ms):
        rows = buckets[start]
        levels = defaultdict(lambda: [Fraction(), Fraction(), Fraction()])
        quote = base = volume = buy = sell = unknown = Fraction()
        evidence = set()
        reasons = set()
        for t in rows:
            qty = number(t["quantity"], positive=True)
            price = number(t["price"], positive=True)
            level = price // step * step
            slot = levels[level]
            slot[0] += qty
            if t["taker_side"] == "Buy":
                buy += qty
                slot[1] += qty
            elif t["taker_side"] == "Sell":
                sell += qty
                slot[2] += qty
            else:
                unknown += qty
            volume += qty
            if inverse:
                quote += qty
                base += qty / price
            else:
                quote += price * qty
                base += qty
            for p in t["provenance"]:
                evidence.add(p["source_ref"])
            if t["coverage"]["status"] != "COMPLETE":
                reasons.add("execution_coverage_not_proven")
            if t["availability"]["status"] != "AVAILABLE":
                reasons.add("execution_availability_not_proven")
            if t["taker_side"] == "Unknown":
                reasons.add("aggressor_unknown")
            if any(p["source_hash"] is None for p in t["provenance"]):
                reasons.add("source_content_hash_missing")
            if t["id_origin"] != "exchange":
                reasons.add("execution_identity_not_exchange")
        delta = buy - sell
        running += delta
        poc = min(levels, key=lambda p: (-levels[p][0], p)) if levels else None
        bars.append({
            "start_ms": start, "end_ms": start + interval_ms,
            "trade_count": len(rows), "volume": decimal(volume),
            "buy_volume": decimal(buy), "sell_volume": decimal(sell),
            "unknown_side_volume": decimal(unknown),
            "delta_known": decimal(delta), "cvd_known": decimal(running),
            "side_classification_complete": unknown == 0 and bool(rows),
            "delta_complete": False,  # Cannot certify the full execution stream.
            "coverage_certified": False,
            "vwap_volume_coverage_certified": False,
            "poc_volume_coverage_certified": False,
            "vwap": decimal(quote / base) if base else None,
            "poc": decimal(poc) if poc is not None else None,
            "footprint": [
                {"price": decimal(p), "volume": decimal(v[0]),
                 "buy_volume": decimal(v[1]), "sell_volume": decimal(v[2]),
                 "unknown_side_volume": decimal(v[0] - v[1] - v[2])}
                for p, v in sorted(levels.items())
            ],
            "evidence_refs": sorted(evidence),
            "reasons": sorted(reasons | ({"empty_interval"} if not rows else set())),
        })
    result = {
        "method": method, "status": "DERIVED_UNVERIFIED",
        "observed_execution_count": len(canonical),
        "cvd_anchor_start_ms": start_ms,
        "cvd_prior_history_known": False,
        "empty_bar_count": sum(not buckets[start] for start in range(start_ms, end_ms, interval_ms)),
        "unknown_side_bar_count": sum(b["unknown_side_volume"] != "0" for b in bars),
        "market": dict(zip(("venue", "category", "symbol"), next(iter(scopes)))),
        "admitted_to_f": False,
        "reasons": ["no_historical_visibility_proof", "no_complete_range_proof"],
        "bars": bars,
    }
    if method == METHOD_V2:
        result["quantity_unit"] = "quote" if inverse else "base"
    return result
