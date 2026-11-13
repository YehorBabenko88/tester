# UI capability discovery contract (Draft PR #74)

The frontend requests `GET /api/capabilities?category=linear&symbol=BTCUSDT&interval=60&start_ms=...&end_ms=...` once per selected range. It expects the existing API envelope `schema_version: "1.0"`, `data: { panels: { ... } }` (or a direct panel map).

Each panel must be explicitly advertised with `panel`, `status` (AVAILABLE, PARTIAL, UNAVAILABLE or UNVERIFIED), a machine-readable lowercase `reason`, and for AVAILABLE/PARTIAL: a verified relative `resource` under `/api/`, immutable `revision`, and `source_ref`. An absent record is unavailable. Do not advertise a resource until the backend has implemented and verified the route and query contract. `resource` is the actual aggregate endpoint, not a guessed mapping. No private or trading endpoints.

Mark/index require independently stored Bybit derivative series; neither trade candles nor last traded prices may be relabeled as mark/index. Footprint, delta, CVD, profile, POC, VWAP require valid underlying executions and provenance; never invent bid/ask or historical completeness. OI/funding availability must reflect backend readiness and source cadence.

## Outstanding acceptance gates

- Implement and independently test backend discovery with source revision, category, symbol, range, interval and route allowlist. Backend currently NOT VERIFIED.
- Verify browser and API requests for all 13 intervals: 1,3,5,15,30,60,120,240,360,720,D,W,M.
- Capture console errors, request/response status and screenshots with an isolated disposable backend; classify expected data-unavailable versus genuine UI exceptions.
- Validate mark/index payload shape (timestamp and decimal `value`) and independently verified provenance.
- Run `node --test frontend/tests/*.test.js` and existing frontend/backend suites; no execution claim from GitHub-only changes.
- Do not deploy to PC4/ADMINIT, modify main, or alter PostgreSQL 5432 as part of this PR.

Status: source changes committed, backend integration and physical/browser acceptance NOT_RUN.