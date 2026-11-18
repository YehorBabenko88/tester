# ADMINIT real Bybit history and derived screener metrics — acceptance contract

## Verified physical state (2026-11-18)
- ADMINIT Preview catalog API: 932 active linear, 26 active inverse instruments; real Bybit /v5/market/instruments-info and /tickers snapshots persisted in dedicated PostgreSQL on loopback 55473.
- The catalog is separate from the historical dataset reader and download queue. `/api/capabilities`: market_catalog.enabled=true, sources.enabled=false, reason=catalog_and_provider_required.
- Browser displays catalog after restoring missing `frontend/capabilities.js`; that file must be integrated into the actual release/installer allowlist, not merely copied manually.
- Screener currently displays 24h price change, volume, turnover and current OI from Bybit tickers. Historical volatility, price change 1h, OI change 1h/24h and market cap are UNAVAILABLE for explicitly different reasons.

## Workstream A: observed metric time-series (catalog owner)
1. Preserve ticker snapshots, timestamps, source URLs, source_time_ms, metric units, and immutable provenance. Never manufacture 1h/24h baselines from a single ticker snapshot.
2. Verify existing `ticker_observations` retention and `enrich_snapshot` 1h/24h baseline joins; add deterministic unit/integration tests with exact source-time windows, ±5 minute tolerance, stale/missing/out-of-order observations, restarts and cross-category isolation.
3. Publish price_change_1h and oi_change_1h/24h only after independent observed baseline exists; until then `UNAVAILABLE: history_not_observed`. Price/volume/OI quantities must keep their distinct units.
4. Volatility requires a documented lookback and return formula over verified candles; do not equate it with Bybit's 24h change or infer from a single price. Market cap requires an independent authoritative supply source and must remain UNAVAILABLE until connected.

## Workstream B: historical data provider and download queue (data owner)
1. Build/attach a read-only Bybit historical source for supported linear/inverse symbols, validated closed OHLCV candles, bounded pagination, rate limits, retry/backoff, TLS, and explicit source provenance.
2. Respect source-specific supported timeframes. Bybit-native intervals are not identical to all 13 UI intervals; unsupported intervals must be explicitly rejected or derived only from verified lower-resolution data with provenance.
3. Use a dedicated queue/storage in the isolated Preview PostgreSQL cluster (non-5432) with explicit migration and separate roles; never assume the catalog-only DB is a fully migrated jobs DB.
4. Persist download job state, observed record counts, checksums, coverage gaps, resume cursor, idempotency keys, and restart recovery; do not mark COMPLETE on merely receiving HTTP 200.
5. No order placement, API trading keys, live execution, or modification of unrelated databases.

## Workstream C: frontend/release owner
1. Keep catalog and historical-source capabilities separate. Show an actionable no-provider message and disable/guard downloads until an actual verified source is configured.
2. Show exact `UNAVAILABLE` reasons and required lookback for volatility, price/OI changes; avoid misleading zeroes.
3. Include `frontend/capabilities.js` in the installer pinned source, packaging manifest, static checks and browser smoke tests. Fail the install if a required imported module returns 404.
4. Validate one-click download with BTCUSDT in an isolated disposable environment; verify progress, restart and chart coverage before promoting to ADMINIT.

## Independent acceptance
- A: deterministic historical metric calculations PASS with source evidence.
- B: actual read-only Bybit historical acquisition and persisted verified closed candles PASS.
- C: browser download -> job -> chart on physical ADMINIT PASS; all imported JS modules HTTP 200.
- Separate checks for restart, no cross-database mutation, and full uninstall.
- Until all pass: historical source/download = NO_GO, regardless of catalog success.
