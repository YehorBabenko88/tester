# DATA #72 → RESEARCH #71 integration contract review

Date: 2026-11-13. Baseline PC4 `3b2eef8a4139195db1a775545e83f1c1f58bf403`.
This document is a review of two **separate Draft PR heads**, not a tested combined implementation.

## Source interfaces examined

- DATA #72 `6322e84d1e89c9d436ebf763064cf27bf47158de`: `src/ingest/public_auxiliary.py`, `decode("trades", ...)` returns `src.storage.models.normalize` envelopes.
- RESEARCH #71 `c6a461b82fae8a2ae39d319fdc7ffb58e1f58909`: `src.analytics.research_metrics.calculate` accepts **canonical analytics trade** objects validated by `src.analytics.core.validate_trade`.

These objects are **not interchangeable**. Directly passing the storage envelope to `calculate` must fail closed.

## Required explicit projection, not automatic coercion

| Storage envelope / source | Analytics canonical trade | Gate |
| --- | --- | --- |
| `venue, category, symbol` | same | Exact match to requested market and persisted query scope |
| `dataset="trades"` | trade-only canonical | Reject funding/OI/mark/index envelopes |
| `timestamp_ms`, original `payload.event_timestamp_ms` | `timestamp_ms` | Equality, original `ms` precision; no fetched-at substitution |
| `payload.trade_id` from `execId` | `trade_id`, `id_origin="exchange"` | No generated exchange ID; duplicate identity conflict must fail |
| `payload.price`, `payload.size` | `price`, `quantity` | Exact positive Decimal strings, no float; unit must be explicit |
| `payload.side` | `taker_side` | Bybit REST taker side Buy/Sell only; unknown must stay Unknown, never infer bid/ask |
| `source.sha256`, `source.evidence_refs`, `source.ingestion_run` | canonical `provenance[]` | Source row hash and original HTTP/page chain must be validated against durable B evidence; source URL alone insufficient |
| `payload.publication_timestamp_ms=None`, `source.observed_at_utc` | separate visibility/knowledge metadata | Unknown publication/visibility cannot be promoted to known-at, coverage COMPLETE or F admission |
| storage evidence `coverage_status="UNKNOWN"` | `coverage.status="UNKNOWN"` | Preserve original requested `[start_ms,end_ms)`, not fabricate completeness from row count |
| storage read status | `availability` | Mark as UNVERIFIED if canonical source/HTTP/DB readback evidence is missing |

## Critical non-equivalences

1. DATA source event payload uses `size` and `side`, while RESEARCH requires `quantity` and `taker_side`.
2. DATA provenance source is a mapping with `source_kind="rest"`, `sha256` and `evidence_refs`; RESEARCH expects `provenance[]` entries with `source_kind="bybit_rest"`, `source_ref`, `source_hash`, `ingestion_run`, `timestamp_precision`, `source_timestamp`. Explicit, verifiable mapping is mandatory.
3. DATA `source.observed_at_utc` is fetch time, not exchange execution time or guaranteed publication time.
4. A recent-trade page is not a complete historical execution stream. All derived footprint/delta/CVD/VWAP/POC outputs are **observed-sample statistics**; they cannot qualify F on real history without independent completeness and visibility receipts.
5. RESEARCH `calculate` returns `delta_known`, `cvd_known` and `admitted_to_f=false`. Keep those names and semantics; never display unqualified total market Delta/CVD.
6. Linear and inverse quantity units may differ. A canonical conversion needs source contract specifications and explicit conversion evidence. Until then, refuse cross-unit aggregation and show UNKNOWN/NOT_COMPARABLE.

## Acceptance matrix for next integration Draft

- Positive: bounded original public Bybit REST page → durable PostgreSQL B record+receipt → read-only projection → `validate_trade` → `calculate`; match exact price/size/side/time and provenance.
- Negative: tampered source row SHA, missing HTTP body proof, duplicate execId with conflicting economics, mixed symbol/category, wrong dataset, absent event timestamp, invented publication timestamp, missing requested coverage bounds, missing source unit, unknown side and inverse/linear unit mismatch.
- Check actual PostgreSQL readback after reconnect and duplicate replay; do not claim source completeness.
- Verify all 13 chart intervals use correct UTC calendar windows; F must reject real `UNKNOWN` history and produce zero approved real simulated fills.
- A passing fixture alone is not actual PostgreSQL/Bybit acceptance. Record `NOT_RUN` for all physical tests not executed.

## Ownership

DATA owns durable source evidence and event/quantity/unit semantics; RESEARCH owns read-only projection and metric computation; MASTER owns exact combined SHA, conflict resolution and no-mock PostgreSQL/browser E2E. No changes to main, PC4, ADMINIT or existing PostgreSQL5432. No Codex used.
