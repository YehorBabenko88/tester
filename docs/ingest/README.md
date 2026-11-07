# Official Bybit ingestion

Use Python 3.11+ and the repository's `requests` dependency. No trading keys, order methods, or authenticated routes are used. The earlier `bybit_history.py` is a legacy PostgreSQL prototype; the supported resumable entry point for this workstream is `python -m src.ingest`.

```powershell
python -m unittest discover -v
python -m tests.ingest.live_smoke
python -m src.ingest discover https://public.bybit.com/trading/BTCUSDT/
python -m src.ingest --journal work/jobs.sqlite archive-range https://public.bybit.com/trading/BTCUSDT/ work/archives --start 2020-03-25 --end 2020-03-26 --workers 3
python -m src.ingest --journal work/jobs.sqlite rest linear BTCUSDT kline --interval 1 --start-ms 1704067200000 --end-ms 1704067500000 --limit 2 --export work/records.jsonl
python -m src.ingest catalog linear
python -m src.ingest --journal work/jobs.sqlite screener linear BTCUSDT
```

All ranges are UTC, start inclusive and end exclusive. `discover` lists one level at a time; follow returned directories for MT4 year folders. Daily and monthly archives can overlap; keep source files separately, deduplicate trades by exchange ID in B's storage. Filename dates are nominal bounds, not verified row coverage. CSV/gzip are supported; ZIP or undocumented formats are not silently accepted.

REST supports every documented candle interval (`1,3,5,15,30,60,120,240,360,720,D,W,M`), OI and account ratio periods (`5min,15min,30min,1h,4h,1d`) and funding. Premium REST is Linear only. Instrument-specific contract types still matter (funding applies to perpetuals). Seven-day windows bound work; each uses cursor or descending timestamp pagination until exhaustion. Empty pages are retried and split, with complete pagination in every subquery. Missing expected timestamps trigger at most 100 repair requests per window per run; persisted records ensure later runs advance. Sparse trading gaps remain unverified, not invented flat candles. Candle closure is checked using Bybit server time, with calendar-aware month/week boundaries. Initial observed timestamp and missing samples are recorded per window; no monotonic-history assumption or binary-search claim is made.

`observed` means the requested acquisition has records and no detected cadence gaps; it is **not** a claim that all official history or every funding event exists. Empty windows and unresolved gaps are `unverified`; transport/API failures raise and journal `failed`. Paused/failed windows replay safely using idempotent record keys. Finished windows and records commit in one SQLite transaction. Historical corrections require a fresh journal or removal of an affected task after review; cached observed windows are not periodically revalidated against REST.

Archives stream to `.partial`, verify size, SHA-256, gzip CRC through EOF, UTF-8 and strict CSV width before atomic publication. Resume requires a successful actual HTTP 206 probe, exact Content-Range and matching strong ETag; If-Range is used. Unsupported Range, weak/missing validators or changed objects restart safely. A locally corrupted cached file is downloaded again. The checksum verifies local byte integrity, not an exchange-signed checksum. Gzip/CSV structure alone cannot establish semantic completeness of exchange records.

Default load is three requests/second shared across threads, 1–8 archive workers (default 3), thread-local HTTP sessions, bounded retry with exponential backoff/jitter, Retry-After handling, Bybit retCode rate-limit handling. HTTP 403 backs off for Bybit's IP protection period. Run a journal/output directory under one process owner; cross-process destination locking and distributed rate limiting belong in E's worker layer.

Screener volatility uses verified, contiguous CLOSED 1m closes: `sqrt(sum(log(C[t]/C[t-1])**2))`, unannualized, fraction units. 1h needs 61 closes and 24h needs 1,441. Missing/nonpositive prices return null. Turnover, 24h price change and current OI remain distinct exchange ticker metrics; 24h OI change compares equal five-minute slots and is null if either endpoint is missing. These are observation metrics, not trading recommendations.

## Integration boundary

[Issue #7](https://github.com/YehorBabenko88/tester/issues/7) records the B/E boundary; B confirmed the approved v1.0 storage contract in [Issue #2](https://github.com/YehorBabenko88/tester/issues/2). `Backfill(API, Journal).run(category,symbol,kind,interval,start,end,limit=None,window_ms=604800000,cancel=None)` supports a cooperative cancellation callback between pages/windows/repairs. HTTP in flight finishes or times out. `Journal.export(path)` emits original-payload JSONL with schema_version='1.0', venue, category, symbol, dataset, interval, UTC timestamp_ms, timestamp_precision, nullable timestamp_ns and source URL/fetched_at/row hash. Decimal values remain strings. Request evidence includes params, cursor, range/count and pagination_status EXHAUSTED/EMPTY/PARTIAL/FAILED/UNVERIFIED; EMPTY never means COMPLETE. The staging SQLite journal is not a migration or substitute for B's PostgreSQL database.

`ArchiveLoader.download(url,destination)` returns archive URL, bytes, SHA-256, rows, columns, fetched_at, ETag and Last-Modified. `ArchiveLoader.many(files,directory,workers=3)` preserves the source directory hierarchy. `iter_trade_records(path,category,symbol,provenance,allow_composite=False)` validates the complete file then streams B v1.0 trades, preserving trdMatchID/id, id_origin='exchange' and original decimal strings. timestamp_ns is populated only for a source timestamp with ns precision; ms/us are never artificially promoted. An explicitly enabled composite hashes category/symbol and sorted original row JSON; identical no-ID trades cannot be distinguished and this fallback cannot establish trade completeness. Journal staging keys use venue/category/symbol/dataset/trade_id, so simultaneous trades survive. Source precedence and PostgreSQL persistence remain B's responsibility. No claim is made that the PostgreSQL integration is already merged.

See [source audit](source-audit.md), [live smoke evidence](smoke-evidence.json) and [boundary evidence](boundary-evidence.json). The audit includes sources outside the implemented historical REST set and distinguishes unsupported history from unverified portal downloads.

Verification note: the stored successful smoke run is timestamped 2026-11-07 13:22 UTC. A later opt-in live rerun encountered OS DNS resolution failures for api.bybit.com/public.bybit.com and was stopped after retry; this environment failure is not an empty market-data response. Unit regressions and validation of already downloaded files still pass. The GitHub CLI credential was invalid; publishing uses the authorized GitHub connector, without exposing or changing any credentials.
