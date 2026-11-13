# UI source capabilities — isolated Draft PR #74

Implements the 2026-11-13 MASTER comment on PR #74 and builds on the
fail-closed foundation of PR #70. This is a read-only **experimental API
extension**; the immutable shared OpenAPI 1.0 document is left unchanged.

## Discovery / authorization

`GET /api/capabilities` accepts the existing validated `Query` scope:
`category`, `symbol`, `interval`, `start_ms`, `end_ms`, and an optional
`source_interval` for OI. It returns an `Envelope` 1.0 with a `data.panels`
map for all 12 terminal panels. The API remains loopback/TrustedHost-only.
Only an explicitly configured, tester-owned provider is eligible for a
qualified result; an injected reader advertisement alone has **no authority**.

Discovery performs **bounded, read-only** source reads (`limit=10`), validates
the canonical response/price-only schema, source scope and declared Bybit
provenance (including original source reference, ingestion run and source
hash). It never downloads, queues jobs, writes to a database, places orders,
or interprets a configured route as proof of existing data. Failure returns
`UNAVAILABLE`/`UNVERIFIED` with a stable reason, not a fabricated dataset.

A successful observed page is marked `PARTIAL`, reason
`bounded_observations_only`. Its `revision=page:<sha256>` addresses only the
**observed page and scope**; `revision_kind=observed_page_digest` expressly
does **not** certify a complete historical dataset or a frozen database
revision. The frontend verifies the source and displays this limitation.

## Routes and panel behavior

| Panel | Qualified source route | Note |
|---|---|---|
| Candles, Volume | `/api/candles`, canonical 1.0 | Volume comes from the same candle response |
| Mark | `/api/price-candles`, canonical 1.1 | Explicit `official_mark`, derivative only |
| Index | `/api/price-candles`, canonical 1.1 | Explicit `official_index`, derivative only |
| Open Interest | `/api/open-interest`, canonical 1.0 | Source cadence is independently bound |
| Funding | `/api/funding`, canonical 1.0 | Event cadence, derivatives only |
| Footprint | Not qualified by the default chart contract | Backend financial query needs frozen cutoff + explicit price-step inputs |
| Delta, CVD, Profile, POC, VWAP | Not qualified | No compatible chart endpoint; never speculate `/api/analytics` |

Mark/Index are projected from validated **closed price-only candles** to a
line using their original decimal `close`. No volume, taker side, Bid/Ask,
full-depth history, inferred trade fill or synthetic price is added. Bar
category/symbol/UTC timestamps, interval and source provenance are checked
again in the frontend. Empty data is reported as empty, not as source tampering.

The optional qualified source contract includes the *exact* read route,
`schema_version`, `candle_kind` (when relevant), content-addressed page
revision and original source reference. Only matching, supported routes can
be invoked. An unavailable capability makes no series request. Aggregate
`/api/coverage` is not requested without a separate capability contract;
coverage available in each response remains visible in that panel.

## Verification

- `tests/platform/test_capability_discovery.py`: real `TestClient` on the
  production FastAPI factory, authorization failures, fake sources, typed
  price-only v1.1 route, Spot restrictions, OI source cadence, all 13 intervals
  and the intentionally invalid request returning 422.
- `frontend/tests/capabilities.test.js` and
  `frontend/tests/capability-wiring.test.js`: pure Node contract,
  precision-preserving price projection, version mismatch, source binding
  and all 13 selectors.
- `frontend/tests/terminal.spec.js`: Playwright source availability fixtures
  and unsupported request suppression.
- `frontend/tests/capability-backend.e2e.js`: Chromium against **real HTTP
  FastAPI routes** on loopback, with a **synthetic, explicitly CI-only**
  source reader. It captures a screenshot and classifies JavaScript errors
  and 404/422/503 API responses across all 13 display intervals.
- `.github/workflows/capability-browser.yml`: runs those checks in hosted
  CI only. It does NOT operate PC4, ADMINIT, office computers or production
  PostgreSQL.

A browser result is **NOT_RUN** until the hosted workflow or an approved
isolated local execution actually finishes. Even a fixture-browser PASS
would not prove physical PC4 acceptance or authentic historical coverage.
Do not merge the Draft PR pending verified CI and independent review.

## Optional UI routes and direct instrument input

The same always-registered `GET /api/capabilities` endpoint can be queried
without an instrument to discover `data.features.market_catalog` and
`data.features.sources`. These flags describe actual mounted optional routes,
not historical data availability. The frontend negotiates these features
**before** attempting `/api/market-catalog/{category}` or `/api/sources`.
When the catalog is not configured the screener is explicitly disabled rather
than generating a speculative HTTP 404. When the source list cannot be safely
used the download source selector reports `source_downloads_not_configured`
and avoids speculative provider 503.

A separate `Direct source symbol` input allows the user to type a validated
symbol such as BTCUSDT and request qualified price data without depending on
the optional catalog. This input is not an instrument listing and does not
assert that the typed symbol actually exists. All selected panels still pass
the same server-side source/interval/price-only capability validation.

The hosted Playwright FastAPI test does not mock the market-catalog HTTP route:
it checks that a disabled catalog is detected, direct instrument selection
can render independently verified **synthetic CI-only** Mark/Index fixtures,
and no unsupported API route responds with 404/422/503. It separately checks
all 13 intervals and records console errors.

### Local execution limitations for the 2026-11-13 verification session

Native Node 22 executed `frontend/tests/capabilities.test.js` on a
locally reproduced, connector-verified snapshot: **6/6 PASS**. An isolated
Chromium `page.set_content` smoke test of that module also passed
4/4 guards with zero console exceptions. These results do **not** certify
the full repository or browser integration.

The full repository cannot be cloned in the execution container:
`git ls-remote` reports `Could not resolve host: github.com`.
A loopback Chromium navigation to an isolated HTTP server is blocked by
`net::ERR_BLOCKED_BY_ADMINISTRATOR`. Consequently full FastAPI and
Playwright integration is **NOT_RUN** locally; the hosted CI jobs remain
the required independent execution gate. No live historical PostgreSQL or
PC4 hardware was touched.

## DATA #72 / RESEARCH #71 exact-source compatibility (19:41 MASTER)

**Reviewed producer heads (pinned by UI CI, not merged):**

- DATA #72: `d08418fc51c890d3390ee5853cf2f9dba6073a80`
- RESEARCH #71: `aecba8f9bdd8ec193f7d2ee36a5dbc64b5366a43`
- UI branch remains based on PR #70; neither producer is copied into the
  committed UI source tree.

The standalone protocol schema is
`docs/ui-capability-protocol-v1.schema.json` with
`data.protocol_version = tester.ui-capabilities/1`.
It does **not** replace `contracts/v1/openapi.json`.
The response contains `data.source_contracts` with exact interface signatures
and `data.observed_page_revision` explicitly identifying a bounded page digest,
not a frozen historical database revision.

### DATA execution evidence bridge

The optional `src.api.capability_data_bridge.observed_execution_evidence`
invokes DATA's exact `src.ingest.execution_contract.read_execution_events`
interface only if DATA has actually been composed into a runtime with a
configured, read-only `Store`. It is **never called by merely copying its
contract declaration**.

Its source input is strictly bounded to a 7-day, 100-event limit. When a page
contains exactly 100 events the result is
`UNVERIFIED/data72_full_page_boundary_unproven` (no continuation witness).
The bridge also fails closed on missing original HTTP/page/row receipts,
unsupported inverse quantity units, publication times presented as known,
cross-scope event IDs, observation times before execution and observations
after an explicitly provided information cutoff. Even a verified smaller
sample returns `UNVERIFIED/data72_observed_sample_not_historical_visibility`
with `historical_coverage=UNKNOWN`, `admitted_to_f=false`, and a
`sample:<sha256>` evidence digest rather than a historical dataset revision.

DATA's `tester-public-execution/1` and RESEARCH's
`tester.execution-metrics/1` are pinned by an additional isolated
`scripts/verify_ui_source_contracts.py` AST signature/provenance gate,
executed against exact independent producer checkouts in the hosted UI
workflow. It checks literal function arguments, versions, limits, quantity
units, immutable receipt fields and explicit unknown coverage. This check
**cannot replace actual original HTTP → disposable PostgreSQL → DATA readback
→ RESEARCH metrics execution on a composed source tree**.

### UI panel policy after composition

Footprint, Delta, CVD, VWAP and POC may eventually display *observed-sample*
research metrics only behind a separately designed, versioned and documented
HTTP read endpoint that supplies exact immutable source/read scope. Current
`/api/analytics` is not an implemented route, and DATA's Python functions
alone do not authorize a browser query. `Profile` likewise remains
unintegrated: RESEARCH #71 does not provide a qualified chart profile route.

Funding and OI DATA #72 are public auxiliary acquisition products, not
`tester-public-execution/1` trades; their provenance and source-native
cadences must be separately verified by a real read endpoint. Neither is
included in execution-only Delta/CVD/VWAP/POC arithmetic.

Mark/Index remain separately qualified price-only v1.1 derivatives, with
closed exchange-time bars, original Decimal strings, true UTC interval
boundaries and no fabricated volume. Their page digest is **not** a shared,
atomic revision with DATA executions or RESEARCH metrics; comparison at
a historical decision time remains unverified.

### Acceptance status

The earlier isolated FastAPI/Chromium E2E on
`9da9054a4ce37ebf56f89f2df278f6c01eb5e4b5` is accepted for UI wiring
only: synthetic price source, 13 timeframes, no browser errors.
It is **NOT** physical Bybit historical acceptance. New source compatibility
checks must pass their own exact-head CI, and MASTER must compose the
independent producer branches and run disposable PostgreSQL source-to-metrics
tests before any historical real-data release can be considered.
