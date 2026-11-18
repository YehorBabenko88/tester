"""Realized volatility from validated, closed, equally spaced candle closes.

Pure calculation only. The caller must supply independently verified candles with
source evidence; this module does not assert that a historical provider exists.
"""

from decimal import Decimal, InvalidOperation
from math import isfinite, log, sqrt


def realized_volatility(candles, *, interval_ms: int, annualize: bool = False):
    """Return fail-closed evidence or an observed population log-return volatility.

    Candles are ascending dicts with timestamp_ms, close (decimal string),
    closed (literal True), and source (nonempty HTTPS URL). A full contiguous
    window of >= 3 candles is required. The returned sigma is dimensionless.
    """

    def unavailable(reason):
        return {
            "state": "UNAVAILABLE",
            "reason": reason,
            "value": None,
            "unit": "ratio",
            "sample_count": 0,
            "annualized": annualize,
        }
    if type(interval_ms) is not int or interval_ms <= 0:
        return unavailable("invalid_interval")
    if not isinstance(candles, (list, tuple)) or not 3 <= len(candles) <= 10000:
        return unavailable("insufficient_closed_candles")
    timestamps, prices, sources = [], [], []
    for row in candles:
        if not isinstance(row, dict) or row.get("closed") is not True:
            return unavailable("unverified_closed_candle")
        ts, src = row.get("timestamp_ms"), row.get("source")
        if (
            type(ts) is not int
            or ts <= 0
            or not isinstance(src, str)
            or not src.startswith("https://")
            or len(src) > 2048
        ):
            return unavailable("missing_candle_provenance")
        try:
            price = Decimal(row["close"])
        except (KeyError, TypeError, ValueError, InvalidOperation):
            return unavailable("invalid_close")
        if not price.is_finite() or price <= 0:
            return unavailable("invalid_close")
        timestamps.append(ts)
        prices.append(price)
        sources.append(src)
    if any(b - a != interval_ms for a, b in zip(timestamps, timestamps[1:])):
        return unavailable("candle_window_gap_or_order")
    try:
        returns = [log(float(b / a)) for a, b in zip(prices, prices[1:])]
        if not all(isfinite(v) for v in returns):
            return unavailable("nonfinite_return")
        avg = sum(returns) / len(returns)
        sigma = sqrt(sum((r - avg) ** 2 for r in returns) / len(returns))
        if annualize:
            sigma *= sqrt(365 * 86400000 / interval_ms)
        if not isfinite(sigma):
            return unavailable("nonfinite_volatility")
    except (OverflowError, ValueError, ZeroDivisionError):
        return unavailable("nonfinite_return")
    return {
        "state": "OBSERVED",
        "reason": None,
        "value": format(sigma, ".15g"),
        "unit": "ratio",
        "sample_count": len(returns),
        "annualized": annualize,
        "window_start_ms": timestamps[0],
        "window_end_ms": timestamps[-1],
        "interval_ms": interval_ms,
        "source_count": len(set(sources)),
    }
