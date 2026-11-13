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
