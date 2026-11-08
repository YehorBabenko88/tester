"""Qualified deltas from genuine source-timestamped ticker observations only."""

import hashlib
import json
import math
from copy import deepcopy
from decimal import Decimal, DecimalException, localcontext

TOLERANCE_MS = 300_000
RETENTION_MS = 26 * 3600_000
MAX_POINTS_PER_CATEGORY = 500_000
DERIVED = {
    "price_change_1h": ("price", 3600_000),
    "oi_change_1h": ("open_interest", 3600_000),
    "oi_change_24h": ("open_interest", 24 * 3600_000),
}


def _decimal(value):
    if not isinstance(value, str) or len(value) > 100:
        return None
    try:
        number = Decimal(value)
        return number if number.is_finite() and abs(number.adjusted()) <= 100 else None
    except DecimalException:
        return None


def _fresh(metric, now):
    stamp, observed = metric.get("source_time_ms"), metric.get("observed_at")
    value = _decimal(metric.get("value"))
    return (
        metric.get("state") == "OBSERVED"
        and type(stamp) is int
        and stamp > 0
        and 0 <= now * 1000 - stamp < TOLERANCE_MS
        and type(observed) in {int, float}
        and math.isfinite(observed)
        and 0 <= now - observed < 300
        and isinstance(metric.get("unit"), str)
        and 0 < len(metric["unit"]) <= 100
        and isinstance(metric.get("source"), str)
        and 0 < len(metric["source"]) <= 2000
        and value is not None
        and value >= 0
    )


def fresh_points(snapshot, now):
    """Group source metric evidence by actual source timestamp, never refresh time."""
    points = {}
    for row in snapshot.get("rows", []):
        for name in ("price", "open_interest"):
            metric = row.get("metrics", {}).get(name, {})
            if not _fresh(metric, now):
                continue
            key = (row["symbol"], metric["source_time_ms"])
            point = points.setdefault(
                key, {"symbol": key[0], "source_time_ms": key[1], "metrics": {}}
            )
            point["metrics"][name] = deepcopy(metric)
    return list(points.values())


def qualify_delta(current, baseline, window_ms):
    result = {
        "value": None,
        "state": "UNAVAILABLE",
        "unit": "ratio",
        "source": current.get("source"),
        "source_time_ms": current.get("source_time_ms"),
        "observed_at": current.get("observed_at"),
        "reason": "history_not_observed",
        "window_ms": window_ms,
        "baseline_source_time_ms": None,
        "actual_window_ms": None,
        "baseline_tolerance_ms": TOLERANCE_MS,
    }
    if current.get("state") != "OBSERVED":
        result["reason"] = "current_observation_unavailable"
        return result
    if baseline is None:
        return result
    if baseline.get("state") != "OBSERVED":
        result["reason"] = "history_observation_unavailable"
        return result
    stamp, before = current.get("source_time_ms"), baseline.get("source_time_ms")
    if (
        type(stamp) is not int
        or type(before) is not int
        or not window_ms <= stamp - before <= window_ms + TOLERANCE_MS
    ):
        result["reason"] = "history_window_unqualified"
        return result
    result["baseline_source_time_ms"], result["actual_window_ms"] = before, stamp - before
    result["baseline_source"] = baseline.get("source")
    result["baseline_unit"] = baseline.get("unit")
    if not current.get("unit") or current["unit"] != baseline.get("unit"):
        result["reason"] = "history_unit_mismatch"
        return result
    if not current.get("source") or current["source"] != baseline.get("source"):
        result["reason"] = "history_source_mismatch"
        return result
    latest, denominator = _decimal(current.get("value")), _decimal(baseline.get("value"))
    if latest is None or denominator is None or latest < 0:
        result["reason"] = "history_value_invalid"
        return result
    if denominator <= 0:
        result["reason"] = "history_baseline_nonpositive"
        return result
    try:
        with localcontext() as context:
            context.prec = 50
            ratio = (latest - denominator) / denominator
            value = format(ratio, ".24g")
    except DecimalException:
        result["reason"] = "history_value_invalid"
        return result
    result.update(
        value=value,
        state="OBSERVED",
        reason=None,
        baseline_value=baseline["value"],
        current_value=current["value"],
    )
    return result


def enrich_snapshot(snapshot, baselines, now):
    for row in snapshot.get("rows", []):
        metrics = row.get("metrics", {})
        for name, (source_name, window_ms) in DERIVED.items():
            current = metrics.get(source_name, {})
            if not _fresh(current, now):
                continue  # Retained STALE values keep their original evidence.
            point = baselines.get((row["symbol"], window_ms, current["source_time_ms"]), {})
            metrics[name] = qualify_delta(current, point.get(source_name), window_ms)
    content = {key: value for key, value in snapshot.items() if key != "revision"}
    snapshot["revision"] = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
    return snapshot
