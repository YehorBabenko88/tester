# Supervisor cycle 2: F and A/B acquisition seam

## Exact scope and result

Read [Issue #16](https://github.com/YehorBabenko88/tester/issues/16) and inspected the complete recursive tree at F SHA `13ffae2cbedea096c9bc81b03933bf3a6488f438` via the GitHub connector. **F is an initial design checkpoint, not an executable Strategy Lab at this pin.** The tree has `docs/strategy/CONTRACT.md`, inherited root app/ingestion files and `test_core.py`; there is no `src/strategy/`, `src/backtest/` or `tests/strategy/`.

Isolated source copies used only under `work/f-cycle2`. No branch, product code, API state, order endpoint, exchange account or shared PostgreSQL instance was modified. No natural-language strategy, DSL or backtest was executed.

## F implementation findings

1. **P1 acceptance blocker — No executable declarative validator or confirmation boundary.** `docs/strategy/CONTRACT.md:7-9` describes validation and backtest POST interfaces, but the exact tree supplies no versioned JSON schema, parser, allowed-operator dispatcher, preview artifact, approval token or engine. Therefore no test can establish unsupported-operator rejection, no-eval/exec enforcement, or that an ambiguous natural-language preview cannot reach a run. This is absent implementation, not a discovered live arbitrary-code-execution path. Keep simulation unavailable until an explicit deterministic DSL exists; ambiguous text must remain preview-only.

2. **P1 acceptance blocker — Deterministic ordering and point-in-time/data-quality gates are design-only.** `CONTRACT.md:15,19-20,25-26` promises reproducibility, closed observations, next-evidence execution, partial rejection and persisted digests. No code enforces a total event order, availability timestamp cutoff, strategy signal/fill delay, duplicate policy, gap policy or VERIFIED/PARTIAL gate at this pin. Nine inherited ingestion tests passing is not evidence of these guarantees. Required tests: equal-time events with source precision/ID ordering; future indicator/pivot knowledge excluded; source available_at after decision rejected; duplicate input rejected or explicitly deduplicated; partial data fails by default; run ID/config/snapshot digest stable across replay.

3. **P1 acceptance blocker — Intrabar and execution-accounting policy is not implemented.** `CONTRACT.md:21-24` states conservative SL/TP, modeled costs and mathematically explicit levels, but contains no fill engine or scorecard. Do not report stop priority, gaps, funding/fees, latency, partial fills, margin or six-month levels as supported. Before acceptance, add a deterministic explicit-DSL fixture where both SL/TP touch the same bar and verify stop-first or unavailable/ambiguous under the chosen contract; add adverse gaps, funding/fee cashflows, no-trade, walk-forward cutoffs and fill/overlay bijection fixtures. Do not substitute optimistic OHLC path assumptions or execute an ambiguous user strategy to test this.

The document correctly labels itself **initial design**. These findings are missing acceptance work, not evidence that its author falsely claimed a finished simulator.

## Executed commands and limits

- GitHub connector read Issue #16 and `git/trees/13ffae2cbedea096c9bc81b03933bf3a6488f438?recursive=1`, then fetched pinned contract/test/import dependencies.
- In `work/f-cycle2`: `../venv/Scripts/python.exe -m unittest discover -v` — **9 methods pass**, exit 0. Seven boundary tests plus two missing-minute ingestion tests. No strategy/backtest test exists or ran.
- `rg -n 'eval\(|exec\(|backtest|strategy|stop.loss|intrabar' .` and numbered contract inspection: strategy matches are documentation only in the copied branch surfaces. Absence of implementation is not a dynamic safety certification.

No trading, ambiguous-prompt execution, network market simulation or PostgreSQL testing was performed.

## Current A/B seam: preserve evidence rather than invent completeness

Reviewed A `addbdda232bc1a604a7a637bdad479752c4cccfe` (`src/ingest/rest.py`, `archive.py`, `journal.py`, previously independently tested 27 methods) against B `5154efab05d44b96f4aa83819672396d75679f2e` (`src/storage/models.py`, `store.py`, `coverage.py`, `docs/storage/contract.md`). These are separate current implementation pins, not the original failing historical stubs.

Concrete mismatches:

- A `rest.py:100` emits `kline` / `long_short_ratio`; B `models.py:10` accepts `candles` / `ratio`. A OHLCV payload is the original array; B `normalize` requires a dict with named OHLC fields. Metric payloads need an exact `value` mapping (openInterest, fundingRate, buyRatio).
- A `archive.py:110-116` puts original ID at envelope `trade_id`; B normalization reads `payload.trade_id`. A `timestamp_fields` reports microseconds without `timestamp_us`; B `models.py` requires exact `timestamp_us` for us precision. Recover it from original timestamp string using an explicit source-format/unit mapping and Decimal/integer conversion, never from rounded ms. Retain timestamp_ns only for genuine ns sources.
- A `rest.py:93,139` stores `requests` with input cursor, bounds, returned count and status. It does not export actual response next cursor explicitly; `Journal.export` (`journal.py:28-30`) emits only record bodies, dropping task evidence. B `store.py:29-61` requires scope, pages, cursor/next_cursor chain and summed response counts. B `ingest:115-123` rejects duplicate record IDs, while its final count must equal summed page counts. A retries/overlapping reprobes/repairs can return a source record more than once but stage it once. Simply mapping all request counts to B pages therefore fails; quietly reducing response counts would destroy acquisition evidence.
- A uses acquisition status `observed`/`unverified`; B requires explicit EXHAUSTED/EMPTY/PARTIAL/FAILED/UNVERIFIED evidence and separate available_range provenance before verification. Neither observed rows nor file nominal dates justify fabricated available_range. B's `coverage.py` independently computes completeness; the adapter must leave unverifiable ranges unknown.

### Minimal lossless adapter contract recommendation (no code changes yet)

Use a versioned **acquisition batch** with immutable `run_id`, `window_id`, exact A/B/adapter SHAs, scope and UTC `[start_ms,end_ms)`, an unmodified raw envelope list, a separately named normalized record list, a complete request-attempt ledger, and explicit acquisition/availability/coverage status. Each normalized record references its original envelope/hash and run/window. Retain every response attempt's URL, original params, incoming and actual outgoing cursor, bounds, raw returned count, returned record identities, terminal reason and fetched_at. Include a source-kind discriminator and timestamp unit/precision; distinguish archive-byte integrity from record completeness.

Perform only explicit field/dataset mappings above; never mutate the source payload in place. Preserve B raw_envelope/source relationships and true exchange IDs. Composite IDs must remain explicitly composite and ineligible to establish completeness; do not map A fallback hashes to B's exchange-ID slot.

At the count seam, retain both `response_row_count` (including retries/duplicates) and `published_unique_record_count`, plus a deterministic membership/dedup map. B needs a reviewed extension to accept the immutable acquisition-attempt ledger alongside a canonical publication batch instead of equating all attempted response counts to unique published records. Until that extension, safely use a clearly identified canonical publication batch while retaining exact raw request attempts as a separate referenced artifact; never relabel its filtered count as an exchange response count.

For time-based pagination, B already permits synthetic **request continuation tokens**. If needed, derive a deterministic adapter request-ordinal token, label it acquisition continuation in params, and preserve the actual exchange cursor separately. Do not fabricate an exchange cursor or infer a terminal response that A did not record. A should capture actual nextPageCursor at acquisition time; an adapter cannot recover omitted terminal data from source records.

Keep unavailable/unknown scope as such until A's metadata/archive manifest and B's semantic checks jointly prove the bounds. Adapter acceptance should replay: repeated response rows across repair, empty response after prior staged rows, microsecond source trade, exact ns trade, source conflict, partial archive, restart checkpoint, and a source payload that fails normalization. Assert no response/evidence loss, no invented precision or availability, stable retry identity and explicit rejected normalization records.


