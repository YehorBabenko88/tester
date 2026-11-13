# Versioned DATA execution contract — `tester-public-execution/1`

Source review: MASTER comment [#72 / 6857982132](https://github.com/YehorBabenko88/tester/pull/72#issuecomment-6857982132), compatibility [PR #75](https://github.com/YehorBabenko88/tester/pull/75), RESEARCH [PR #71](https://github.com/YehorBabenko88/tester/pull/71).

**DATA writes original public Bybit REST execution rows and source evidence to `tester_storage`.** RESEARCH must not pass those storage envelopes directly to `src.analytics.research_metrics.calculate`.

## Public event contract

`src.ingest.execution_contract.read_execution_events(Store(dsn), category="linear", symbol="BTCUSDT", start_ms=..., end_ms=..., limit=...)`

- `contract_version = "tester-public-execution/1"`, exact `venue/category/symbol`, `exec_id` from original Bybit `execId`.
- `taker_side` is **Buy**, **Sell**, or **Unknown**. It is *not* reconstructed Bid/Ask or maker identity.
- `price` and `quantity` are exact positive canonical Decimal strings. Original source `size` is retained in the immutable payload, never processed through a float.
- `quantity_unit` is `source_native_linear_size` or `source_native_inverse_contract`. The inverse/native conversion is not certified; RESEARCH projection rejects inverse.
- `event_ms` is original exchange `time` in milliseconds, `observed_ms` is the real UTC HTTP retrieval instant in milliseconds, and `publication_ms` is **null**. The latter is not inferred from the former.
- `source_receipts[]` binds `original HTTP SHA256`, canonical `page SHA256`, canonical `row SHA256`, durable `evidence_id` and `page_ref`. The bridge rehashes the stored original HTTP bytes, checks the original row inside the page, validates the Bybit endpoint and query parameters, and rejects modified immutable `record_key`/`content_hash`.
- `utc_interval = {start_ms, end_ms}` uses half-open UTC epoch milliseconds; `source_coverage.status = UNKNOWN` always, `availability.status = UNVERIFIED` always. A recent-trade page never proves completeness or source publication/visibility at a historical trading decision.

## RESEARCH consumption

`src.ingest.execution_contract.read_research_trades(store, category="linear", symbol="BTCUSDT", start_ms=..., end_ms=..., limit=...)` makes a read-only, scope-bounded PostgreSQL query and validates its projection using the existing `src.analytics.core.validate_trade` contract. Its output renames `size -> quantity` and `side -> taker_side` only **after** verifying original persisted evidence. The `provenance[]` entries become canonical `bybit_rest` objects with preserved SHA256 and original millisecond source timestamps, plus parent evidence refs.

This projection is for **observed-sample research statistics only**. Real historical F strategy admission must reject `UNKNOWN` history and unknown publication/visibility. A projection or fixture is **not** a qualified F approval.

## Admission controls and tests

- No private/API-key endpoint. No real trading.
- Read-only `Store.read` with explicit category, symbol, `[start_ms,end_ms)`, ≤7-day query window and ≤100 rows.
- Reject missing/mismatched HTTP proof, tampered row/page hashes, absent event time, invented publication time, missing source quantity unit, incompatible inverse quantity, wrong scope/dataset, conflicting `execId` and missing source bounds.
- Offline tests: `python -m unittest tests.test_execution_contract_bridge -v`.
- Disposable PostgreSQL: `TESTER_AUX_TEST_DSN` is provided only by the dedicated CI PostgreSQL service; `python -m unittest tests.test_execution_contract_postgres -v` performs source-to-PG replay and reconnect/readback with the original HTTP source bytes.
- All physical 1→10 Bybit samples, full browser checks and release acceptance remain **NOT_RUN** until executed and measured. Full history remains `UNKNOWN`.

Only PR #72 branch is modified. No main/PC4/ADMINIT/production database mutation; no cross-branch merge.
