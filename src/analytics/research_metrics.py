"""Execution-derived research metrics; never infer aggressor or historical coverage.

Pure bounded calculations. This is NOT a real-data F admission authority.
"""
from collections import defaultdict
from fractions import Fraction

from src.analytics.core import deduplicate, validate_trade
from src.analytics.exact import decimal, number

METHOD = "tester.execution-metrics/1"
MAX_TRADES = 100_000
MAX_BARS = 1000


def calculate(trades, *, start_ms: int, end_ms: int, interval_ms: int, tick_size: str):
    """Return deterministic footprint, delta, CVD, VWAP and volume POC.

    A taker Buy is positive delta; Sell negative. Unknown taker direction
    contributes to volume/VWAP/POC but NOT delta. No Bid/Ask reconstruction.
    Coverage/availability is reported separately and never promoted.
    """
    if any(type(x) is not int for x in (start_ms, end_ms, interval_ms)):
        raise ValueError("integer millisecond bounds required")
    if start_ms < 0 or end_ms <= start_ms or interval_ms <= 0:
        raise ValueError("invalid bounds")
    if (end_ms - start_ms) % interval_ms:
        raise ValueError("unaligned observation window")
    if (end_ms - start_ms) // interval_ms > MAX_BARS:
        raise ValueError("bar limit exceeded")
    step = number(tick_size, positive=True)
    if not isinstance(trades, list):
        raise ValueError("canonical_execution_list_required")
    if len(trades) > MAX_TRADES:
        raise ValueError("execution limit exceeded")
    canonical = deduplicate([validate_trade(t) for t in trades])
    if not canonical:
        return {"method": METHOD, "status": "NO_DATA", "admitted_to_f": False,
                "bars": [], "reasons": ["no_executions"]}
    scopes = {(t["venue"], t["category"], t["symbol"]) for t in canonical}
    if len(scopes) != 1:
        raise ValueError("mixed market identities")
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
        pv = volume = buy = sell = unknown = Fraction()
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
            pv += price * qty
            for p in t["provenance"]:
                evidence.add(p["source_ref"])
            if t["coverage"]["status"] != "COMPLETE":
                reasons.add("execution_coverage_not_proven")
            if t["availability"]["status"] != "AVAILABLE":
                reasons.add("execution_availability_not_proven")
            if t["taker_side"] == "Unknown":
                reasons.add("aggressor_unknown")
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
            "vwap": decimal(pv / volume) if volume else None,
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
    return {
        "method": METHOD, "status": "DERIVED_UNVERIFIED",
        "market": dict(zip(("venue", "category", "symbol"), next(iter(scopes)))),
        "admitted_to_f": False,
        "reasons": ["no_historical_visibility_proof", "no_complete_range_proof"],
        "bars": bars,
    }
