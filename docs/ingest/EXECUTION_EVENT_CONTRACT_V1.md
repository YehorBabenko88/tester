# Versioned DATA execution contract — `tester-public-execution/1`

Source review: MASTER comment [#72 / 6857982132](https://github.com/YehorBabenko88/tester/pull/72#issuecomment-6857982132), compatibility [PR #75](https://github.com/YehorBabenko88/tester/pull/75), RESEARCH [PR #71](https://github.com/YehorBabenko88/tester/pull/71).

**DATA writes original public Bybit REST execution rows and source evidence to `tester_storage`.** RESEARCH must not pass those storage envelopes directly to `src.analytics.research_metrics.calculate`.

## Public event contract

`src.ingest.execution_contract.read_execution_events(Store(dsn), category="linear", symbol="BTCUSDT", start_ms=..., end_ms=..., limit=...)`

- `contract_version = "tester-public-execution/1"`, exact `venue/category/symbol`, `exec_id` from original Bybit `execId`.
- `taker_side` is **Buy**, **Sell**, or **Unknown**. It is *not* reconstructed Bid/Ask or maker identity.
- `price` and `quantity` are exact positive canonical Decimal strings. Original source `size` is retained in the immutable payload, never processed through a float.
- `quantity_unit` is `source_native_linear_size` or `source_native_inverse_contract`. The inverse/native conversion is not certified; RESEARCH projection rejects inverse.
- `event_ms` is original exchange `time` in milliseconds, `observed_ms` is the real UTC HTTP retrieval instant in milliseconds, and `publication_ms` is **null**. The latter is not inferred from the former. A persisted observation whose `observed_ms < event_ms` fails closed: no future event is presented as already known.
- `source_receipts[]` binds `original HTTP SHA256`, canonical `page SHA256`, canonical `row SHA256`, durable `evidence_id` and `page_ref`. The bridge rehashes the stored original HTTP bytes, checks the original row inside the page, validates the Bybit endpoint and query parameters, and rejects modified immutable `record_key`/`content_hash`.
- `utc_interval = {start_ms, end_ms}` uses half-open UTC epoch milliseconds; `source_coverage.status = UNKNOWN` always, `availability.status = UNVERIFIED` always. A recent-trade page never proves completeness or source publication/visibility at a historical trading decision.

## RESEARCH consumption

`src.ingest.execution_contract.read_research_trades(store, category="linear", symbol="BTCUSDT", start_ms=..., end_ms=..., limit=...)` makes a read-only, scope-bounded PostgreSQL query and validates its projection using the existing `src.analytics.core.validate_trade` contract. Its output renames `size -> quantity` and `side -> taker_side` only **after** verifying original persisted evidence. The `provenance[]` entries become canonical `bybit_rest` objects with preserved SHA256 and original millisecond source timestamps, plus parent evidence refs.

This projection is for **observed-sample research statistics only**. The `read_research_trades` entry point rejects an exact-full `limit` result: a bounded SELECT cannot prove that a following execution page is empty. RESEARCH #71 also rejects full pages and does not promote `UNKNOWN` to complete history. Real historical F strategy admission must reject `UNKNOWN` history and unknown publication/visibility. A projection or fixture is **not** a qualified F approval.

## Admission controls and tests

- No private/API-key endpoint. No real trading.
- Read-only `Store.read` with explicit category, symbol, `[start_ms,end_ms)`, ≤7-day query window and ≤100 rows.
- Reject missing/mismatched HTTP proof, tampered row/page hashes, absent event time, invented publication time, missing source quantity unit, incompatible inverse quantity, wrong scope/dataset, conflicting `execId` and missing source bounds.
- Offline tests: `python -m unittest tests.test_execution_contract_bridge -v`.
- Disposable PostgreSQL: `TESTER_AUX_TEST_DSN` is provided only by the dedicated CI PostgreSQL service; `python -m unittest tests.test_execution_contract_postgres -v` performs source-to-PG replay and reconnect/readback with the original HTTP source bytes.
- All physical 1→10 Bybit samples, full browser checks and release acceptance remain **NOT_RUN** until executed and measured. Full history remains `UNKNOWN`.

Only PR #72 branch is modified. No main/PC4/ADMINIT/production database mutation; no cross-branch merge.

## Disposable, pinned DATA + RESEARCH end-to-end checks

The PR-local workflow `.github/workflows/public-auxiliary-data.yml` provides separate PostgreSQL 16 jobs:

- `postgres-contract` runs DATA's offline recorded-HTTP → immutable PG roundtrip independently.
- `research-composed-postgres` uses an ephemeral checkout of exact RESEARCH SHA `ba82b02f0e2eec7b6f1ef1c6276cdc30e736a7ad`, copies only the tested RESEARCH consumer/metric files **inside the ephemeral CI runner**, and asserts original HTTP → PG16 → reconnect → `read_data_metrics` → exact VWAP/POC/known Delta/CVD, bounded-page refusal, late-observation refusal and `admitted_to_f=false`. No branches are merged or updated.
- `live-bybit-postgres` uses only unauthenticated public Bybit V5 HTTPS, starts with BTCUSDT, then checks ten public linear symbols, stores original HTTP/row/page proofs in a separate disposable database, reads them back and invokes the pinned RESEARCH consumer; Funding and OI use bounded two-page probes. This job has one-request/second throttling and cancels superseded PR instances. **Live PASS is not claimed until the GitHub runner actually executes it.** Geographical/public endpoint unavailability must be reported as FAIL/BLOCKED, not replaced with synthetic fixtures.

No host PC4, ADMINIT, existing PostgreSQL port 5432 or production deployment is used. CI remains distinct from physical acceptance and does not certify market completeness.
