# Historical Laboratory UI

A local, read-only browser client. Serve this directory at `/` from agent E's local API (same origin). No external CDN, trading key, raw tick download, or browser-side financial aggregation is used. No deployment is performed by this PR.

## Controls
Search/category/sort, instrument checkboxes and Save watchlist; saved identities include category. The screener displays one bounded server page at a time and keeps at most 20 row elements in the DOM. Search/sort parameters are sent upstream; current E API ignores these extra parameters, so fallback search/order is explicitly limited to the current page. Next page replaces the current page and preserves selection. Hover or focus a metric to inspect source, observation time, units and availability. Unverified metric envelopes display unavailable.

Select a source and data kind, then Download History creates one E job per selected instrument. Pause/Resume/Retry operate on E's durable job IDs. IDs are stored locally (maximum 100 tracked jobs); job polling is serialized, four requests at most concurrently, terminal jobs only on explicit refresh. No browser estimate of completion or ETA is invented. Numeric legacy progress is capped at 99% without explicit verified completion. Unknown byte counts, throughput and verification counts remain unknown. Counters refer to upstream-reported received bytes, not database size.

Select a symbol name for charts. All panels share the same UTC range and timeframe. Buttons, wheel zoom, drag pan and Shift-drag range selection request new bounded server ranges; superseded requests are cancelled. Range fields are explicitly UTC, including in locales whose datetime widgets use local-looking formatting. End is exclusive. Maximum 1000 rows per panel response, 8 MB per HTTP response, 20-second request timeout. Pagination is flagged partial instead of fetching arbitrary history. Choose a larger timeframe or narrower range to obtain a complete bounded response. Decimal strings are preserved; Number conversions only project values to canvas pixels. Exact source records remain available under each panel.

## E API compatibility and remaining integration
Current v1 endpoints consumed: GET `/api/instruments`, `/api/sources`, `/api/candles`, `/api/footprint`, `/api/open-interest`, `/api/funding`, `/api/coverage`; POST `/api/downloads`; GET `/api/downloads/{id}`; POST `/{pause,resume,retry}` under each download. E has no job-list GET; only IDs created by this browser are restored/polled.
Envelope: `{schema_version:"1.0", data, next_cursor}`. E array rows use `timestamp_ms` and `provenance`; funding uses `rate`, footprint flat rows use `price,buy_volume,sell_volume`. The client groups footprint rows for display only; it never recomputes taker volumes, delta, CVD, VWAP, profile or POC. E candle arrays provide OHLCV. OI rows use value. Without explicit coverage metadata, nonempty legacy arrays are PARTIAL, as_of UNKNOWN, closure UNKNOWN. Empty arrays are DATA_UNAVAILABLE, never confirmed complete.

Required optional extension proposed in Issue #4, pending E/C integration: `/api/analytics?metric=delta|cvd|profile|poc|vwap|mark|index` with standard range Query. `data` is `{availability,reason,source,as_of,completeness,rows}`. Generic rows: `{timestamp_ms,value}`; profile: `{timestamp_ms,price,volume,is_poc}`. Same-timestamp price bins are supported. Structured candle/footprint/OI/funding envelopes may use the same metadata. Candle rows may provide `is_closed` and `gap_before`. Footprint envelope rows use `{timestamp_ms,levels:[{price,taker_buy,taker_sell}]}`. No rows are invented when extension is absent: the real 404/503 reason is displayed. No `/api/trades` request is made.

Screener extension: instrument `metrics[name] = {value:decimal_string,source,as_of:epoch_ms,availability:AVAILABLE|PARTIAL,unit,reason?,formula?}`. Names: price,volatility,change_1h,change_24h,volume,turnover,open_interest,oi_change_1h,oi_change_24h,market_cap. Market cap requires a verified external source/asset mapping. No formula is performed locally.

Job extensions: uppercase DISCOVERING/DOWNLOADING/VERIFYING/COMPLETE/PARTIAL/FAILED plus PAUSED; `progress:{completed,total,verified}` or legacy numeric fraction with `verified:true`; `sources:[{source,state,progress,bytes,verified_records,bytes_per_second,error}]`; `counters:{bytes,verified_records,bytes_per_second,eta_seconds,eta_confident}`. Legacy queued/running/paused/completed/failed states map to UI labels but are not promoted to verified success. Provider errors are visible. Coverage is shown independently of raw/derived labels: local record hashing does not prove historical completeness.

## Tests
From `frontend/`, Node 22+:

```powershell
npm ci
$env:PLAYWRIGHT_BROWSERS_PATH="$PWD/.browsers"
npx playwright install chromium
npm test
npm run test:ui
```

Playwright intercepts the test origin and serves the actual HTML/ES modules from disk, with fixtures confined to `tests/`. It requires no listening port or live database and tests browser behavior, not a duplicated implementation. Actual E/A/B/C provider and PostgreSQL end-to-end verification remains an integration dependency, not claimed by these tests. Optional `UI_SCREENSHOT` saves an explicitly marked test-fixture preview. `.browsers`, node_modules and test artifacts are ignored. No runtime dependencies or build step.
