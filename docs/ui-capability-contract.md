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

- DATA #72: `4e57f1665b1e15bc1b42b0f2329a92f8b6942d24`
- RESEARCH #71: `4d215df51726689768f6e7b6085b0e574f24c284`
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

### Producer pin refresh and original-source evidence scope

The DATA and RESEARCH heads above were refreshed after checking a direct
GitHub ancestry comparison: the changes since the prior pins were restricted
to their respective workflows and tests, not the DATA execution contract,
RESEARCH adapter or metrics implementation. Each new pin is independently
checked out by the UI hosted workflow and tested against
`scripts/verify_ui_source_contracts.py` without committing those sources.

The optional `data.execution_source_evidence` result is a separately
scoped read-only call into DATA's versioned execution bridge only when
`src.ingest.execution_contract` and a suitable `Store` are present.
It checks original row/page/HTTP receipt hashes, matching provenance,
canonical Decimal text, source-native linear quantity, the half-open time
scope and knowledge-time cutoff. Even with successful readback it returns
`UNVERIFIED` and a bounded `sample:` digest; it does not create or
authorize `/api/analytics`, nor certify complete history.

The browser evidence JSON records the exact UI checkout SHA and the
reviewed DATA/RESEARCH producer SHAs separately. Previous synthetic
screenshots and source checks are not a proxy for live Bybit historical
PostgreSQL readback across the three branches.

## Contract-owner Kind v1.1 proposal — no legacy v1 mutation

Contract owner review is requested in [Issue #76](https://github.com/YehorBabenko88/tester/issues/76).
The original `contracts/v1/openapi.json` explicitly admits only
`candles,trades,footprint,open_interest,funding` for
`DownloadRequest.kind`, whereas `src/api/models.py` already allows the
additional `mark,index` values on the inherited PC4 baseline.

The proposal at `docs/proposals/download-kind-v1_1.json` specifies an
**inactive** opt-in `/api/v1_1/downloads` version and never silently adds
values to the pinned v1 enum. This matters because older generated clients
may treat an unknown enum value as a fatal validation error. The
`scripts/compare_download_kind_proposal.py` and
`tests/platform/test_download_kind_proposal.py` verify that the sole
runtime-versus-pinned OpenAPI delta is the two values, and that neither a
versioned route nor real Mark/Index downloads have been authorized.

The existing price-only, closed source candles in
`GET /api/price-candles` schema 1.1 are a distinct verified read interface;
they neither imply that any bulk download workflow can ingest those prices
nor provide a historical source-completeness witness. Contract-owner
approval and new source/storage E2E are required before activating the
proposed download version. The shared v1 OpenAPI and financial-query schema
remain unchanged.

## Preparation for genuine observed research plots

The pure `frontend/observed-research.js` adapter understands the reviewed
DATA #72 / RESEARCH #71 `tester.data-research-preview/2` sample contract.
It transforms exact Decimal and aligned UTC observation bars into
Footprint, *known-taker* Delta, *window-anchored known* CVD, VWAP and POC
chart rows. It requires original DATA72 HTTP/page/row receipt verification
and valid SHA256 evidence digests. It never reconstructs Bid/Ask, assigns
UNKNOWN taker side to Buy/Sell, upgrades `UNKNOWN` coverage, or authorizes
trading. RESEARCH monthly-variable bars are deliberately refused.

This adapter currently issues **no network requests** and is not yet
mounted into a browser panel: the independent UI branch has no qualified
RESEARCH HTTP endpoint. `frontend/tests/observed-research.test.js` checks
precision, unknown side, source version, mismatched UTC boundaries and all
13 declared intervals (monthly correctly UNAVAILABLE). A real plotted
sample remains dependent on MASTER composing an approved DATA/RESEARCH
read endpoint with source identity, versioning, provenance and authorization.
Funding and OI require a separate source-native auxiliary read contract
and must never enter execution-only VWAP/Delta calculations.

### DATA #72 auxiliary OI / Funding observed-value projection

`frontend/observed-auxiliary.js` is the second, separate **pure/no-fetch**
UI adapter. It projects DATA's actual canonical `normalize()` record shape
for public V5 `open_interest` and `funding` into exact native
`{ts,value}` observations. It requires the correct `payload.unit`:
`contracts_source_native` (OI) or `fraction_per_funding_event` (Funding).
Funding may be negative; OI may not. OI's native source cadence
(`5min,15min,30min,1h,4h,1d`) is **not** silently replaced with the
terminal's display timeframe, and Funding retains its event cadence.

The adapter checks the source's Bybit HTTPS URL, category, symbol and native
cadence, canonical SHA256 hashes and immutable record identity, original
millisecond source timestamp, aware observation time, half-open requested UTC
scope and optional knowledge cutoff. It preserves Decimal strings without
floating-point conversion, and emits no inferred volume or OHLC. Duplicate
observations, unverified publication, late-known data and wrong source
provenance fail closed. Its state is `OBSERVED_UNVERIFIED` and explicitly
`original_http_attested=false` because a normalized row hash is **not** an
independent proof of the original HTTP/page bytes.

`frontend/tests/observed-auxiliary.test.js` covers both datasets over all
13 **display** timeframes and the negative source/known-at cases. The
adapter does not add any browser request or qualify the existing OI/Funding
API routes by its existence. Activation requires the DATA owner to expose
an audited original-HTTP-proven, read-only, versioned auxiliary endpoint
and for MASTER to run exact combined FastAPI/Chromium with disposable PG.
No source completeness or production availability is claimed.

## MASTER 2026-11-14 05:58 — versioned contract drift guard

The exact independent source pins tested by the UI workflow are DATA #72 `37fde4a9efba72eaf1b25a1b00e07385b07fd572` and RESEARCH #71 `b11b79832d1b0d848773ff077a36e7cb0595fdcb`; the DATA execution-contract blob is `8dc34f5fc73c4d9a1199efb8081be63ecdbf1d79`, the RESEARCH consumer blob is `c043f4eeac2c4119eb9fa4dd0983677cad3e40b0`, and its metrics blob is `d565c1239b60e770c0337423c5e014daeec0e457`. These are separately asserted in CI, not copied into the UI branch and not a release-authorized combined revision. The latest DATA and RESEARCH integration suites still have independent full-acceptance gates.

The proposed v1.1 `DownloadRequest.kind` extension remains **inactive**, pending contract-owner Issue #76. `compare_download_kind_proposal.py` now examines actual runtime OpenAPI paths to detect unauthorized activation of `/api/v1_1/downloads`; it does not hard-code that route as absent. Dedicated Python tests check this detection and preserve all five original v1 kind values. The immutable `contracts/v1/openapi.json`, financial-query v1 schema and existing download route are not edited.

If MASTER later approves v1.1, its contract should be independently published with explicit opt-in routing, producer capability admission and client schema migration. `mark/index` continue to use existing price-only GET `/api/price-candles` v1.1 with original Decimal and UTC source/provenance; **no** fabricated volume, no implied download authorization.

No real DATA/RESEARCH metrics HTTP endpoint is composed here. All Footprint/known Delta/window-anchored CVD/VWAP/POC and Funding/OI transforms remain pure no-fetch observed-sample adapters, deliberately displaying `UNAVAILABLE` or `UNVERIFIED` unless approved readback/route evidence exists. The UI must never guess `/api/analytics`, historical completeness, or an F grant. Synthetic closed mark/index fixtures in Chromium are UI contract evidence, not original live Bybit history.

## MASTER #77 combined F-G-D 404 diagnosis and UI regression (2026-11-14)

At staging c8cfcb76, `scripts.combined_browser_ci.application` creates the market app with `market_catalog_enabled=False`, but baseline frontend `app.js` unconditionally calls `GET /api/market-catalog/{category}` on load. That route is not mounted, producing `404 API_ERROR` in the market screener. The older Chromium test also asserted the obsolete heading `/Tester/i`, whereas the real page contains `HISTORICAL LAB`. This is independently visible in the exact combined CI failure, and must not be hidden with a fake success response.

UI #74's discovery-first bootstrap checks `GET /api/capabilities` before attempting optional catalog/source endpoints. A new `frontend/tests/combined-backend.e2e.js` regression uses the real disposable PostgreSQL-backed market+research ASGI composition, verifies the disabled catalog and all 13 UTC chart timeframes, and records any unexpected `404/422/503` or unsupported catalog/analytics requests. It also checks unauthorized research submit/read `401`, forged artifact digest ACK `409`, and the existing positive G-owned exact artifact/markers/render ACK, which is verified against the PostgreSQL ledger. The test keeps the true synthetic/fixture source label, not a live Bybit or complete-history claim. The timeframe control is inside collapsed `#download-options`; the combined test opens it before selecting, just as the targeted UI E2E does.

The full shared OpenAPI v1 remains immutable. Only the inactive proposed `/api/v1_1/downloads` Kind-extension documented in Issue #76 is allowed to add `mark/index` later with contract-owner approval. This UI draft neither registers that route nor modifies generated v1 clients. Staging #77 still needs MASTER's immutable composition review and cannot inherit standalone UI PASS without actual combined CI.
