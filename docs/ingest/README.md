# Official Bybit ingestion

Use Python 3.11+ and the repository's `requests` dependency. No trading keys, order methods, or authenticated routes are used. The earlier root `bybit_history.py` legacy PostgreSQL prototype was removed in PR #102 (no equivalence evidence; see docs/release/consolidation.md); the supported resumable entry point for this workstream is `python -m src.ingest`.

```powershell
python -m unittest discover -v
python -m tests.ingest.live_smoke
python -m src.ingest discover https://public.bybit.com/trading/BTCUSDT/
python -m src.ingest --journal work/jobs.sqlite archive-range https://public.bybit.com/trading/BTCUSDT/ work/archives --start 2020-03-25 --end 2020-03-26 --workers 3
python -m src.ingest --journal work/jobs.sqlite rest linear BTCUSDT kline --interval 1 --start-ms 1704067200000 --end-ms 1704067500000 --limit 2 --export work/handoff.json
python -m src.ingest catalog linear
python -m src.ingest --journal work/jobs.sqlite screener linear BTCUSDT
```

All ranges are UTC, start inclusive and end exclusive. `discover` lists one level at a time; follow returned directories for MT4 year folders. Daily and monthly archives can overlap; keep source files separately, deduplicate trades by exchange ID in B's storage. Filename dates are nominal bounds, not verified row coverage; for daily trade archives `iter_trade_records` enforces that every row lies inside the named UTC date. CSV/gzip are supported; ZIP or undocumented formats are not silently accepted.

REST supports every documented candle interval (`1,3,5,15,30,60,120,240,360,720,D,W,M`), OI and account ratio periods (`5min,15min,30min,1h,4h,1d`) and funding. Premium REST is Linear only. Instrument-specific contract types still matter (funding applies to perpetuals). Seven-day windows bound work; each uses cursor or descending timestamp pagination until exhaustion. Empty pages are retried and split, with complete pagination in every subquery. Missing expected timestamps trigger at most 100 repair requests per window per run; persisted records ensure later runs advance. Sparse trading gaps remain unverified, not invented flat candles. Candle closure is checked using Bybit server time, with calendar-aware month/week boundaries. Initial observed timestamp and missing samples are recorded per window; no monotonic-history assumption or binary-search claim is made. Each response is checked as a whole before any row is staged (#113 GAP-1/GAP-2): a row outside the requested window, a row of an interval-sampled dataset (klines, OI, account ratio) off the UTC interval grid, or a timestamp repeated with a different payload within one response or one window attempt fails the window (`failed`, run raises); identical repeats are deduplicated. Still-open candles are never staged and are exempt from the cross-response comparison.

`observed` means the requested acquisition has records and no detected cadence gaps; it is **not** a claim that all official history or every funding event exists. Empty windows and unresolved gaps are `unverified`; transport/API failures raise and journal `failed`. Paused/failed windows replay safely using idempotent record keys. Finished windows and records commit in one SQLite transaction. Historical corrections require a fresh journal or removal of an affected task after review; cached observed windows are not periodically revalidated against REST.

Archives stream to `.partial`, verify size, SHA-256, gzip CRC through EOF, UTF-8 and strict CSV width before atomic publication. Resume requires a successful actual HTTP 206 probe, exact Content-Range and matching strong ETag; If-Range is used. Unsupported Range, weak/missing validators or changed objects restart safely. A locally corrupted cached file is downloaded again. The checksum verifies local byte integrity, not an exchange-signed checksum. Gzip/CSV structure alone cannot establish semantic completeness of exchange records.

Default load is three requests/second shared across threads, 1–8 archive workers (default 3), thread-local HTTP sessions, bounded retry with exponential backoff/jitter, Retry-After handling, Bybit retCode rate-limit handling. HTTP 403 backs off for Bybit's IP protection period. Run a journal/output directory under one process owner; cross-process destination locking and distributed rate limiting belong in E's worker layer.

Screener volatility uses verified, contiguous CLOSED 1m closes: `sqrt(sum(log(C[t]/C[t-1])**2))`, unannualized, fraction units. 1h needs 61 closes and 24h needs 1,441. Missing/nonpositive prices return null. Turnover, 24h price change and current OI remain distinct exchange ticker metrics; 24h OI change compares equal five-minute slots and is null if either endpoint is missing. These are observation metrics, not trading recommendations.

## Integration boundary

The newer immutable A3098→B e236 original-run contract is separately exercised on a disposable PostgreSQL cluster, including actual server restart: see [pinned pair command and evidence](pinned-A3098-B-e236.md). This synthetic compatibility check does not replace the earlier live evidence or prove full-pipeline acceptance.

[Issue #7](https://github.com/YehorBabenko88/tester/issues/7) records the B/E boundary; B confirmed the approved v1.0 storage contract in [Issue #2](https://github.com/YehorBabenko88/tester/issues/2). `Backfill(API, Journal).run(category,symbol,kind,interval,start,end,limit=None,window_ms=604800000,cancel=None)` supports a cooperative cancellation callback between pages/windows/repairs. HTTP in flight finishes or times out. **`Journal.export(path)` now emits a self-contained atomic JSON package, replacing the earlier unsafe row-only JSONL.** The package preserves raw versions, original archive/HTTP bytes/hash, runs, task attempts and actual request/cursor/count evidence; its records carry separate availability/coverage and resolvable evidence/run references. Strict shared-v1 records and B5154efab named-payload records are separate explicit projections. See [handoff contract and migration](handoff-contract.md) for exact mapping, fixture pins and unresolved shared candle-schema limitations. The staging SQLite journal is not a migration or substitute for B's PostgreSQL database.

`ArchiveLoader.download(url,destination)` returns archive URL, bytes, SHA-256, rows, columns, fetched_at, ETag and Last-Modified. `ArchiveLoader.many(files,directory,workers=3)` preserves the source directory hierarchy. `iter_trade_records(path,category,symbol,provenance,allow_composite=False)` validates the complete file, binds it to the single UTC date in the file name or `provenance['url']` name (`archive_utc_day`; no date, two dates or disagreeing names are rejected) and rejects the whole file before the first record if any row lies outside that date (#113 GAP-3), then streams B v1.0 trades, preserving trdMatchID/id, id_origin='exchange' and original decimal strings. timestamp_ns is populated only for a source timestamp with ns precision; ms/us are never artificially promoted. An explicitly enabled composite hashes category/symbol and sorted original row JSON; identical no-ID trades cannot be distinguished and this fallback cannot establish trade completeness. Journal staging keys use venue/category/symbol/dataset/trade_id, so simultaneous trades survive. Source precedence and PostgreSQL persistence remain B's responsibility. No claim is made that the PostgreSQL integration is already merged.

See [source audit](source-audit.md), [original live smoke evidence](smoke-evidence.json), [boundary evidence](boundary-evidence.json) and the [new A-to-B handoff evidence](handoff-live-evidence.json). The audit includes sources outside the implemented historical REST set and distinguishes unsupported history from unverified portal downloads.

Historical verification note: the original successful smoke run is timestamped 2026-11-07 13:22 UTC; a later rerun encountered DNS failures, explicitly preserved as an environment failure. The subsequent PR10 correction performed a fresh successful live retrieval and A-export-to-B PostgreSQL18.4 replay test; its separately timestamped report is linked above. Neither success claims global historical completeness.

Opt-in canonical current instrument export: see [metadata-handoff.md](metadata-handoff.md), including the Spot quantity-step limitation, source evidence and B-compatible artifacts.

## Single canonical Bybit transport and writers (issues #104, #105, #109)

- **One HTTP executor.** Every public Bybit request (REST backfill, catalog, lab downloads, `public_auxiliary`) goes through `src.ingest.transport.Transport`. Its sessions come only from `src.ingest.transport.public_session()` (`trust_env=False`, native OS trust via `SystemTrustAdapter`, or an explicit pinned CA bundle); `transport_policy.native_public_session()` and `lab_market.verified_session()` delegate to it, and `bybit_catalog` no longer mutates caller sessions. In releases `configured_public_transport()` binds the durable installation-wide PostgreSQL policy (dispatch lease, 429 cooldown, persistent 403/TLS halt).
- **retCode handling.** `rest.API.call` (Backfill/metadata: bounded retCode retry within one `Transport.operation()` budget) and `BybitCatalogSource.call` (single attempt; scheduler owns retries, 429/10006 → `rate_limited`) keep separate application-level retCode mapping on top of the same Transport. Consolidating them changes catalog retry semantics and was deliberately not done in this change.
- **Endpoint allowlist.** `src.ingest.public_access` allows `/v5/market/recent-trade` only with `category` ∈ {spot, linear, inverse}, an uppercase `symbol`, and `limit` ≤ 60 (spot) / 1000 (others); no time range or cursor.
- **public_auxiliary** is restricted to anonymous recent-trade executions. Funding and open interest have exactly one producer/normalizer: `rest.Backfill` → journal/`handoff.storage_record` → `Store.ingest`; a second writer with a different payload for the same `record_key` would raise `DataConflict`. The former OI cursor traversal (and its pages[0].cursor receipt, Guardian EX-AUX-05) was removed together with that duplicate writer.
- **public_auxiliary_runner** refuses to touch the network without the durable policy (`TESTER_AUX_POLICY_DSN` + `TESTER_AUX_POLICY_INSTALLATION_ID`, validated by `PostgresTransportPolicy`) and requires `require_auth=scram-sha-256` on its disposable DSN.
- **Bounded execution reads.** `execution_contract.read_execution_events` uses `Store.read_bounded` (`src/storage/bounded_read.py`): row/source/evidence bytes, observation fan-out per record and in total, distinct evidence documents and wall time are checked by SQL aggregates before any document is transferred; `/api/capabilities` uses a tighter budget and reports `data72_read_budget_exceeded` with the named budget.

## Inverse executions (units)

`public_auxiliary` stores inverse recent-trade rows through the same canonical
`Store.ingest` path as linear, with `payload.unit = source_native_inverse_contract`
(Bybit inverse `size` = contracts of 1 USD, i.e. quote units). The DATA
execution contract declares `RESEARCH_CATEGORIES = ("linear", "inverse")` and
`SOURCE_QUANTITY_UNITS`; spot has no execution producer and is refused. Unit
table per layer and the VWAP/footprint/delta conventions:
[docs/integration/INVERSE_UNITS.md](../integration/INVERSE_UNITS.md).
