# Offline synthetic catalog query measurement

Measured 2026-11-08 at runtime source `21492bbdc983e1a4bd202b16a2f612458dfb5f7a`
using Python 3.14.0 on Windows. This evidence is synthetic and offline. It does
not measure Bybit, HTTP handling/response serialization, PostgreSQL, browser rendering,
concurrency or an installed service. No latency SLA or overall acceptance is asserted.
The existing catalog correctness fixture exercises 1200 rows without recording timings;
the existing queue benchmark requires PostgreSQL and was not used here.

The fixture generates 10000 Trading linear instruments with source-like metric
provenance, five status acquisition scopes, deterministic permuted prices and null
prices on every seventeenth symbol. Every query validates its total and exact returned
symbol sequence against an independently computed full-dataset model. The assertions
cover global sorting before pagination, null placement, filtering and stable all-null
sorting; every returned page has at most 100 rows.

Each case has one untimed warm-up and seven timed sequential samples. Timings include
the correctness assertions. A separate tracemalloc pass measures peak Python query
allocation; its tracing overhead is excluded from the timing samples. At this sample
size, nearest-rank p95 equals the maximum and is not a reliable tail-latency estimate.

| Query | Median ms | Maximum ms | Separate Python allocation peak MiB | Sampled own-process RSS MiB |
| --- | ---: | ---: | ---: | ---: |
| Symbol first page | 349.0 | 415.8 | 42.67 | 103.2 |
| Global price descending, first page | 483.6 | 501.0 | 42.67 | 130.4 |
| Global price descending, final page | 463.4 | 490.4 | 42.67 | 136.5 |
| Search then global price sort | 367.1 | 480.0 | 42.67 | 114.8 |
| Three-symbol watchlist then global price sort | 352.4 | 409.7 | 42.67 | 108.9 |
| All-null metric, stable descending sort | 407.5 | 469.0 | 42.67 | 111.6 |

Synthetic acquisition took 309.4 ms across 15 fixture calls. Serialized snapshot size
was 20602132 bytes (19.65 MiB). Own-process RSS was 55.0 MiB before fixture creation and
95.0 MiB after snapshot creation/size measurement. RSS was sampled after query calls;
these values are not true peak RSS or an OS-enforced resource limit. The script aborts
if a sampled value exceeds 768 MiB. Process RSS includes Python/imports/allocator
retention, while tracemalloc reports a separate query's Python allocations only.

The query copies the full snapshot before applying filters, so even selective search
and three-symbol watchlist queries have about the same measured allocation peak as an
unfiltered query. Pagination bounds returned rows but does not bound this snapshot
copy cost. This is a measured scaling characteristic, not a claim of a memory leak or
a hard performance failure. Hardware, competing load and allocator behavior can change
the results; the benchmark records all samples and does not tune or change runtime code.

Reproduce from the benchmark commit with an unused output filename:

```powershell
python scripts/benchmark_catalog.py --rows 10000 --iterations 7 --out work/catalog-benchmark-new.json
```

The output directory must already exist; existing output files are never overwritten.
The JSON captures runtime Git SHA, benchmark file SHA256, Python/platform, every timing
sample, allocation measurements and explicit scope limitations. Only this benchmark
process's memory is read; no other process, database, network or listener is contacted.
The committed raw measurement is `docs/evidence/issue40/catalog-synthetic-10000-2026-11-08.json`.
