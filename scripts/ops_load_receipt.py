"""Read-only sustained-load receipt collection for OPS acceptance.

No trading, process control, database mutation or external network calls.
A caller supplies observed samples from an isolated tester instance.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

class EvidenceRefusal(RuntimeError):
    pass

@dataclass(frozen=True)
class LoadSample:
    symbol: str
    observed_monotonic_s: float
    latency_ms: float
    rss_bytes: int
    cpu_percent: float
    success: bool

def evaluate_load(samples: Iterable[LoadSample], expected_symbols: set[str],
                  min_duration_s: float = 600.0, min_per_symbol: int = 20,
                  max_failure_ratio: float = 0.01) -> dict:
    """Reject sparse, synthetic-looking coverage; report observed measurements only.

    This validates receipt shape, not provenance or whether observations are real.
    An independent operator must attest the data source and process ownership.
    """
    rows = list(samples)
    if len(expected_symbols) != 10 or min_duration_s <= 0 or min_per_symbol <= 0:
        raise EvidenceRefusal("Invalid ten-symbol acceptance contract")
    if not 0 <= max_failure_ratio <= 1:
        raise EvidenceRefusal("Invalid failure budget")
    if not rows or any(row.symbol not in expected_symbols or
                       not isinstance(row.observed_monotonic_s, (float, int)) or
                       not isinstance(row.latency_ms, (float, int)) or
                       row.latency_ms < 0 or row.rss_bytes < 0 or
                       row.cpu_percent < 0 or not isinstance(row.success, bool)
                       for row in rows):
        raise EvidenceRefusal("Malformed or unexpected sample")
    import math
    if any(not math.isfinite(row.observed_monotonic_s) or
           not math.isfinite(row.latency_ms) or
           not math.isfinite(row.cpu_percent) for row in rows):
        raise EvidenceRefusal("Nonfinite sample")
    counts = Counter(row.symbol for row in rows)
    if any(counts[symbol] < min_per_symbol for symbol in expected_symbols):
        raise EvidenceRefusal("Insufficient per-symbol observations")
    duration = max(row.observed_monotonic_s for row in rows) - min(
        row.observed_monotonic_s for row in rows
    )
    if duration < min_duration_s:
        raise EvidenceRefusal("Insufficient sustained observation duration")
    failures = sum(not row.success for row in rows)
    if failures / len(rows) > max_failure_ratio:
        raise EvidenceRefusal("Failure budget exceeded")
    latencies = sorted(row.latency_ms for row in rows)
    def pct(p: float) -> float:
        return latencies[min(len(latencies)-1, int((len(latencies)-1)*p))]
    return {"status": "OBSERVED_ONLY", "sample_count": len(rows),
            "symbol_count": len(counts), "duration_s": duration,
            "failures": failures, "failure_ratio": failures / len(rows),
            "p50_latency_ms": pct(.5), "p95_latency_ms": pct(.95),
            "p99_latency_ms": pct(.99),
            "peak_rss_bytes": max(row.rss_bytes for row in rows),
            "peak_cpu_percent": max(row.cpu_percent for row in rows),
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "provenance_verified": False}
