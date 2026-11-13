# RESEARCH #71 ↔ DATA #72 — versioned public execution adapter

**Status:** Draft/NO_GO. No main merge, no PC4/ADMINIT/office changes, no orders.

- MASTER scope: https://github.com/YehorBabenko88/tester/pull/71#issuecomment-6857982889
- Interface review: https://github.com/YehorBabenko88/tester/pull/75
- DATA owner: https://github.com/YehorBabenko88/tester/pull/72
- DATA versioned contract reviewed at **9193622249a5c659d53b9848c13fd363a86f3b4a**;
  schema **`tester-public-execution/1`**.
- Independent RESEARCH branch does **not** copy DATA source files into Git.
  Only the isolated hosted CI job composes those exact files into a temporary
  checkout, with no commit, merge, office/service access or live network feed.

## Runtime consumer

`src.integration.data_research_adapter.read_data_metrics(store, category="linear",
symbol="BTCUSDT", start_ms=..., end_ms=..., interval_ms=60000,
tick_size="0.5", limit=100, as_of_ms=None)`

The method **requires** the versioned DATA module in the composed candidate.
Without it the operation refuses with `data72_versioned_contract_not_composed`.
It does **not** silently use the older `source_native_quantity` DATA draft or
convert its storage envelope into a trade.

DATA is responsible for its bounded, SELECT-only PostgreSQL read and for
verifying the *original* HTTP bytes, canonical page/row SHA256, immutable B
record/evidence, run linkage, source identity, millisecond event time and
source-native quantity unit. RESEARCH independently validates DATA's event
schema, cross-checks DATA's `to_research_trade` projection and computes exact
Fraction/Decimal arithmetic with deterministic price-bin POC tie-break.

### Data-to-metrics semantics

| Input | Consumer rule |
| --- | --- |
| `exec_id` | Original anonymous public exchange trade ID; duplicates rejected |
| `taker_side` | Buy = positive known delta, Sell = negative, Unknown contributes only to volume |
| `quantity` | Canonical exact source-native linear size; inverse contract sizes refused |
| `event_ms` | Exchange time; never substituted with `observed_ms` |
| `observed_ms` | HTTP receipt time; optional `as_of_ms` gate rejects late observation |
| `publication_ms=None` | UNKNOWN; historical visibility never inferred |
| `source_receipts` | Require event/page/HTTP/row hashes and durable evidence IDs |
| `source_coverage.status=UNKNOWN` | No historical completeness certification |
| `availability.status=UNVERIFIED` | Not suitable for qualified real-history F |

Each result includes a bounded observed-window count and first/last seen event,
plus `hit_bounded_event_limit`. **These are observed sample diagnostics, not
proofs of full market coverage.** CVD begins at the requested window start,
not at exchange inception. VWAP and POC refer only to observed volume;
Footprint/Delta never invent Bid/Ask or maker attribution.

No F input, G/E job or real fills are produced: `admitted_to_f=false`,
`f_input=null`, `g_e_result=null`. Output `source_verification` only means
the persisted HTTP/page/row *integrity chain* was checked by DATA. It does
**not** mean Bybit has cryptographically attested the bytes, that publication
time is known or that all trades in the interval were captured.

## Verification matrix

- Pure source: exact VWAP/POC/Delta/CVD, unknown side, event reorder,
  missing receipts, malformed SHA, wrong category, inverse unit, missing or
  fabricated publication time, late knowledge, duplicate IDs, zero quantity,
  invalid scope, explicit 100-event and seven-day bounds.
- **Composed positive** (conditional in standalone RESEARCH PR): DATA's
  actual original HTTP/page/row fixture `read_execution_events` →
  `to_research_trade` equality → RESEARCH calculations. No network mocks for
  physical acceptance; a pure `Mock` here is explicitly unit testing.
- **Actual PostgreSQL acceptance** (only on ephemeral GitHub Actions PG16):
  original HTTP response replay → DATA `fetch_and_ingest` with repeated
  job → new `Store(dsn)` → versioned DATA source validation →
  RESEARCH VWAP/Delta. Uses only dedicated
  `tester_research_data_disposable` with the CI-supplied DSN.
- Isolated `Research Issue 59 isolated checks` workflow checks Python
  compile, focused pytest, Ruff, versioned DATA unit tests and actual
  disposable PG readback when a runner executes. Runner **QUEUED/PENDING**
  is **NOT_RUN**, not PASS.
- Real 1→10 instrument Bybit acquisition, complete archive proof, actual
  physical PC4 PostgreSQL, all 13 chart rendering modes, F/G/E real-history
  replay and browser E2E remain **NOT_RUN / NOT_ACCEPTED**.

## Composition ownership

DATA #72 owns producer/receipt and native quantity semantics. RESEARCH #71 owns
the deterministic observed-sample metrics and fail-closed projection. MASTER
owns exact combined SHA selection, disposable PG/browser acceptance and release
decision. Do not merge drafts or modify PC4/main. No Codex.

## Follow-up validation gates (2026-11-13)

The RESEARCH consumer now rejects a DATA event read containing **exactly the
requested row limit** (`data_event_page_limit_requires_pagination`). DATA's
bounded `Store.read` does not return an end-of-stream witness for that query,
so a full page is a potentially truncated observed sample. The request should
be narrowed or DATA should provide a separately verified paginated interface.
A **zero-row** read carries `source_verification=NO_EXECUTION_EVIDENCE`; it is
not an authenticated proof of an empty market. Duplicate receipt tuples are
rejected, and reported nonempty/empty bar counts are explicitly sample
activity, never historic source completeness. These guards have focused unit
tests in `tests/platform/test_data_research_adapter_issue59.py`.

The independent local container cannot resolve GitHub DNS, so local pytest is
**NOT_RUN**; pushed GitHub Actions are in **QUEUED/PENDING** state, not PASS.
The separate PostgreSQL16 workflow is disposable CI only and does not touch
PC4, ADMINIT, PostgreSQL5432 on user hardware or `main`.
