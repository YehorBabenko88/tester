# Current instrument metadata handoff

Issue1 marker `A-0a6d-canonical-metadata-handoff`. Acquisition is opt-in; imports and ordinary tests perform no network, installation or real database work. Existing raw `catalog` behavior remains available. No B/shared files or authoritative database are created/changed.

```powershell
python -m src.ingest --journal work/metadata-jobs.sqlite catalog-handoff linear --symbol BTCUSDT --max-pages 1 --max-rows 2 --limit 1 --export work/linear-metadata.json
python -m src.ingest --journal work/metadata-jobs.sqlite catalog-handoff inverse --symbol BTCUSD --max-pages 1 --max-rows 2 --limit 1 --export work/inverse-metadata.json
python -m src.ingest --journal work/metadata-jobs.sqlite catalog-handoff spot --symbol BTCUSDT --max-pages 1 --max-rows 2 --export work/spot-metadata.json
```

Each command explicitly acquires public data and writes its named acquisition journal/artifact. It never calls B or trading endpoints. `max_pages` is required (1–100); derivative page limit 1–1000, maximum processed rows 1–100000. Spot sends neither limit nor cursor. Existing API/Transport apply their bounded retry/rate policy. Page/row caps and terminal cursors are recorded, never COMPLETE. A row-cap acquisition emits no canonical/storage metadata because omitted rows could conceal conflicts; original whole HTTP bodies remain available.

## Official source semantics

Checked 2026-11-07 against [Bybit instruments-info](https://bybit-exchange.github.io/docs/v5/market/instrument):

| Category | Price increment | Quantity increment | Scope/status |
| --- | --- | --- | --- |
| Spot | priceFilter.tickSize | No documented qtyStep mapping; basePrecision is base-coin precision | Current queried snapshot, no pagination |
| Linear | priceFilter.tickSize | lotSizeFilter.qtyStep | Cursor pagination, explicit request filters |
| Inverse | priceFilter.tickSize | lotSizeFilter.qtyStep | Cursor pagination, explicit request filters |

Do not infer increments from minOrderQty, priceScale or decimal-place counts. Strings are preserved exactly, including trailing zeros. Spot raw rows are retained but cannot satisfy shared1.0's mandatory positive qty_step without inventing semantics. A future nullable/unknown quantity-step contract would require cross-agent review; this change does not modify that contract. Missing category, status or steps cannot produce canonical records. launchTime/deliveryTime remain raw metadata; they do not establish historical listing completeness or past rule activation. Default endpoint status filtering is exchange-defined and may change; exact sent filters and returned row statuses are preserved.

## Artifact and consumer contract

`schema_version=1.0`, `artifact_kind=bybit_instrument_handoff` is an acquisition envelope, not a new shared DB schema. `instruments` contains strict shared1.0 instrument records (validated against immutable shared9766c009 in tests). `pages` preserves original HTTP bytes as base64, original_http_body SHA-256, URL, exact request, acquisition time, server source timestamp with ms precision, and pagination/terminal evidence. `raw_observations` links processed original rows by page ref/index. `attempts` includes failures; available failed-response bytes are retained. A transport failure may have no HTTP body. `run` is the actual existing Journal run, not an export-time substitute.

`storage_metadata` entries are exact keyword args for **B e236395 Store.save_instrument**: venue, category, symbol, effective_ms, metadata, source. Consumer example after its own validation/authorization: `store.save_instrument(**artifact)`. The source contains the acquisition run, original hash/hash scope, precise fetched_at, source timestamp, scope, evidence refs and a self-contained acquisition_manifest (all pages/attempts/terminal). Metadata contains the strict instrument, original row versions and field mapping. effective_ms is the first local acquisition time rounded down to ms, **not** exchange rule activation/launch time; source.fetched_at retains acquisition precision. The exchange response envelope time is preserved separately.

Identical same-symbol rows merge provenance; different raw same-symbol versions within the acquisition are quarantined, including a malformed second version. No last-write-wins. Re-export the same artifact for idempotent replay: no run/time/hash changes occur. Each actual reacquisition gets a new run/time and therefore a new B revision even if market fields did not change; this distinguishes fresh observations from replay. B's existing revision digest/ON CONFLICT semantics remain unchanged.

## Verification and limits

58 A unit methods pass, including 14 metadata methods. They cover category/step validation, exact strings, scope/hash/time failures, duplicates/conflicts, row/page caps, repeated cursors, sparse/empty pages, malformed result, network failure after a good page, stable artifact re-export and revision behavior. The pinned actual B method executes against a mocked connection; exact kwargs/source/metadata are checked, stable revision on replay and a new revision on changed metadata are confirmed. Its instrument is validated by the actual pinned shared validator. This is not a PostgreSQL roundtrip claim.

A separate explicitly invoked live smoke made three successful public requests at 2026-11-07T16:30Z: Spot BTCUSDT raw1/canonical0, Linear BTCUSDT raw1/canonical1, Inverse BTCUSD raw1/canonical1. All remain PARTIAL with UNKNOWN coverage; historical catalog/rule history is UNVERIFIED. Full original responses and runs are in metadata-evidence/*.json. SHA-256 proves byte integrity, not an exchange signature. Synthetic tests and real smoke evidence are separate. No full-pipeline acceptance, DB publication, installation, TI/trading, merge or service operations were performed.
