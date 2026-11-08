"""Offline synthetic catalog query measurements; no network, database or listener."""

import argparse
import hashlib
import json
import math
import platform
import statistics
import subprocess
import sys
import time
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.api.market_catalog import CatalogService  # noqa: E402

NOW = 1_000_000


class FixtureSource:
    def __init__(self, size):
        self.size, self.calls = size, 0

    def call(self, endpoint, params):
        self.calls += 1
        common = {"category": params["category"], "_source_time_ms": NOW * 1000}
        if endpoint == "/tickers":
            return dict(
                common,
                list=[
                    {
                        "symbol": f"S{i:05}USDT",
                        "lastPrice": None if i % 17 == 0 else str((i * 7919) % self.size),
                        "volume24h": str(i),
                        "openInterest": str(i * 3),
                    }
                    for i in range(self.size)
                ],
            ), "fixture://tickers"
        start = int(params.get("cursor", "0"))
        end = min(start + 1000, self.size)
        active = params["status"] == "Trading"
        return dict(
            common,
            list=[
                {
                    "symbol": f"S{i:05}USDT",
                    "status": "Trading",
                    "contractType": "LinearPerpetual",
                    "baseCoin": "SYNTH",
                    "quoteCoin": "USDT",
                    "settleCoin": "USDT",
                }
                for i in range(start, end)
            ]
            if active
            else [],
            nextPageCursor=str(end) if active and end < self.size else "",
        ), "fixture://instruments"


def benchmark(size=10000, iterations=7):
    if not 1000 <= size <= 10000 or not 1 <= iterations <= 10:
        raise ValueError("Bounded fixture requires 1000..10000 rows and 1..10 iterations")
    process = psutil.Process()
    rss_before = process.memory_info().rss
    source = FixtureSource(size)
    service = CatalogService(source, clock=lambda: NOW)
    started = time.perf_counter()
    if not service.refresh("linear"):
        raise RuntimeError("Synthetic acquisition failed")
    acquisition_ms = (time.perf_counter() - started) * 1000
    snapshot = service._snapshots["linear"]
    serialized_bytes = len(json.dumps(snapshot).encode())
    rss_after = process.memory_info().rss
    # Symbol model is independent of returned pages; numeric values are integers.
    ids = list(range(size))
    prices = {i: None if i % 17 == 0 else (i * 7919) % size for i in ids}
    price_desc = sorted((i for i in ids if prices[i] is not None), key=lambda i: (-prices[i], i))
    price_desc += [i for i in ids if prices[i] is None]
    cases = [
        ("symbol_first_page", dict(limit=100), ids, 0),
        (
            "price_desc_global_first_page",
            dict(sort="price", direction="desc", limit=100),
            price_desc,
            0,
        ),
        (
            "price_desc_global_late_page",
            dict(sort="price", direction="desc", offset=size - 100, limit=100),
            price_desc,
            size - 100,
        ),
        (
            "search_global_then_price",
            dict(search="009", sort="price", direction="desc", limit=100),
            [i for i in price_desc if "009" in f"S{i:05}USDT"],
            0,
        ),
        (
            "watchlist_global_then_price",
            dict(
                watchlist=[f"linear:S{i:05}USDT" for i in (1, size - 1, size // 2)],
                sort="price",
                direction="desc",
                limit=100,
            ),
            [i for i in price_desc if i in {1, size - 1, size // 2}],
            0,
        ),
        (
            "all_null_metric_stable_sort",
            dict(sort="market_cap", direction="desc", limit=100),
            ids,
            0,
        ),
    ]
    results = []
    for name, kwargs, expected, offset in cases:

        def query():
            result = service.query("linear", **kwargs)
            actual = [r["symbol"] for r in result["rows"]]
            target = [f"S{i:05}USDT" for i in expected[offset : offset + 100]]
            if result["total"] != len(expected) or actual != target or len(actual) > 100:
                raise RuntimeError(f"Global query correctness failed: {name}")
            return result

        query()  # Untimed warm-up; retained service snapshot is reused.
        timings = []
        sampled_rss = process.memory_info().rss
        for _ in range(iterations):
            start = time.perf_counter()
            result = query()
            timings.append((time.perf_counter() - start) * 1000)
            sampled_rss = max(sampled_rss, process.memory_info().rss)
            if sampled_rss > 768 * 1024**2:
                raise MemoryError("Own benchmark process exceeded 768 MiB sampled RSS budget")
        # Separate allocation pass: tracing overhead is excluded from timed samples.
        tracemalloc.start()
        query()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        ordered = sorted(timings)
        results.append(
            dict(
                name=name,
                query=kwargs,
                total=result["total"],
                returned=len(result["rows"]),
                min_ms=min(timings),
                median_ms=statistics.median(timings),
                p95_nearest_rank_ms=ordered[math.ceil(0.95 * len(ordered)) - 1],
                max_ms=max(timings),
                samples_ms=timings,
                traced_peak_query_alloc_bytes=peak,
                sampled_process_rss_bytes=sampled_rss,
            )
        )
    return dict(
        schema="tester-catalog-synthetic-benchmark/1",
        observed_at=datetime.now(UTC).isoformat(),
        synthetic=True,
        source_sha=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        benchmark_file_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        python=platform.python_version(),
        platform=platform.system(),
        rows=size,
        iterations=iterations,
        page_limit=100,
        source_calls=source.calls,
        acquisition_ms=acquisition_ms,
        serialized_snapshot_bytes=serialized_bytes,
        rss_before_fixture_bytes=rss_before,
        rss_after_snapshot_bytes=rss_after,
        cases=results,
        boundaries={"network_calls": 0, "database_connections": 0, "listeners": 0},
        limitations=[
            "Single process sequential in-memory CatalogService.query only; no HTTP/JSON response encoding, PostgreSQL, UI, browser or concurrent load acceptance.",
            "RSS is own-process sampling after calls, not a true RSS high-water mark or OS-enforced limit.",
            "tracemalloc peak is Python query allocation from a separate slower tracing pass, not total process RAM.",
            "Seven samples by default; nearest-rank p95 equals maximum for this small sample; no latency SLA asserted.",
        ],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=10000)
    parser.add_argument("--iterations", type=int, default=7)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = benchmark(args.rows, args.iterations)
    with args.out.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
    print(
        json.dumps(
            {
                "rows": report["rows"],
                "cases": [
                    {
                        "name": c["name"],
                        "median_ms": c["median_ms"],
                        "peak_alloc_mib": c["traced_peak_query_alloc_bytes"] / 1024**2,
                    }
                    for c in report["cases"]
                ],
            },
            indent=2,
        )
    )
