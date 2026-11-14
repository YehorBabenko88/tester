# RESEARCH #59 — evidence-first DATA → UI observation bridge (not an HTTP route)

Scope: Draft PR #71 only. Immutable DATA #72 review HEAD:
`f78349b1ad7bb754baee380f59bca67f2b1d2009`. Compared to the
previous pin `0d7ff246...`, only two DATA workflow files and
`tests/storage/test_postgres.py` differ. The DATA public execution
parser, migration `008_public_unknown_taker_side.sql`, public ingestion
and storage models are unchanged.

## Display-only contract

`src/integration/research_ui_projection.py` defines
`tester.research-ui-observation/1`, a **pure, read-only**, bounded
projection of `read_data_metrics()` output. This is neither a new FastAPI
endpoint nor a G/E control command.

- Require DATA72 persisted original-HTTP/page/row receipts and four
  qualified observation/digest fingerprints. Caller-supplied
  `project_events()` fixtures cannot be presented as source-backed.
- Check the metric digest, unbroken UTC bar boundaries, exact decimal
  volume conservation, known Buy−Sell Delta, CVD anchored only at the
  requested window, per-price footprint conservation and deterministic
  lower-price POC tie-break. Never round or coerce to binary float.
- Preserve real `Unknown` taker-side volume and non-certification flags.
  No synthetic Bid/Ask, exchange completeness, publication time, persisted
  dataset revision or historical known-at is invented.
- Produce explicit `f_admission=DENIED_UNKNOWN_COVERAGE` and
  `g_e_execution=NOT_AUTHORIZED`, with `http_route=null` and
  `http_route_status=NOT_REGISTERED`. It cannot authorize Strategy Lab F,
  dispatch G, acknowledge E or fabricate a mounted UI endpoint.

The intended entrypoint is **read_verified_ui_observation(store, ...)**:
it invokes DATA72's persisted original-HTTP/page/row readback through
`read_data_metrics` first, and only then projects the display contract.
The lower-level `project_source_observation(preview)` checks internal
shape and arithmetic but **does not cryptographically authenticate a
caller-supplied dictionary**: fabricating a `source_verification`
string must never authorize a route, trading job or DATA capability.
Only the readback entrypoint may serve future API integration.

Standalone shape tests include valid exact arithmetic, unknown side,
Decimal VWAP, observed CVD, invalid and rehashed metrics, tampered
footprint/POC/UTC/digests, forged coverage/known-at/F/G/E admission and
failure to promote unauthenticated fixtures. The existing composed DATA
unit and real PostgreSQL integration cases also exercise the trusted
readback entrypoint, assert equality to the derived projection, reject
lookahead `as_of_ms`, and retain the F/G/E restrictions.

## Combined F-G-D browser HTTP 404 — independent routing failure

The staging combined browser CI at PR #77 HEAD `c8cfcb76d0d92028fee8e254a66732442ed184f3`
was not used as proof of RESEARCH failure. Inspection of the frozen
combined CI source shows:

1. `frontend/app.js:loadInstruments()` automatically calls
   `GET /api/market-catalog/linear` on the root market workspace.
2. `scripts/combined_browser_ci.py:application()` creates the market
   application with `market_catalog_enabled=False`.
3. `src/api/main.py:create_app()` mounts the market-catalog router only
   when that flag enables a configured catalog. Therefore the browser
   visibly receives `404 API_ERROR: Not Found` before the authenticated
   synthetic F→G→D flow is assessed.
4. `frontend/tests/combined-backend.e2e.js` additionally asserts the
   home page contains `/Tester/i`, while the actual HTML heading shows
   `HISTORICAL LAB / BYBIT MARKET RESEARCH`.

The catalog route/fixture alignment belongs to the combined CI/UI
composition owner, not the RESEARCH financial calculation. A missing
route must never be made to return invented successful market data.
The separately composed research control endpoints do not authorize
calling unmounted market-catalog resources.

## Independent pinned Research PostgreSQL integration

MASTER also identified a separate `PGHOSTADDR` test assumption:
GitHub's PostgreSQL container can report internal `172.x` from
`inet_server_addr()`, while the pinned **libpq client-side endpoint**
really is `127.0.0.1:55472`. The integration test now checks
`conn.info.host`, `conn.info.hostaddr`, `conn.info.port` and
`conn.info.dbname` against the validated DSN while deliberately setting
`PGHOSTADDR=127.0.0.2`. It still queries `current_database()` and verifies
that no foreign 127.0.0.2 server has been reached. This removes the false
server-side address comparison without weakening the runtime
`validate_research_dsn` restriction or `psycopg.connect(hostaddr=...)`
override. Actual Pinned Research PostgreSQL CI is required before claiming
a corrected integration.

## Release gates

Independent Research Issue59 producer CI previously passed for
`b11b79832d1b0d848773ff077a36e7cb0595fdcb`:
77 passed / 4 skipped, Ruff PASS; 10 DATA contract and 4 composed real
PostgreSQL E2E PASS. New exact HEAD must pass again before a target PASS
claim.

Real complete historical DATA/B atomic revision and publication time
remain UNKNOWN. Bybit REST 403 remains ACCESS_BLOCKED. Strategy Lab F
REAL remains DENIED; G/E and UI should not run any real historical
simulation until an independently reviewed complete-evidence contract
is available. No main/PC4/PG5432 changes, no Codex and no trading.


## MASTER PR #77 exact overlap resolution (2026-11-14)

Conflict path: `tests/platform/test_research_postgres.py`, touched by
DATA Draft #72 and RESEARCH Draft #71. **Do not apply last-writer-wins.**
DATA updated the file during review multiple times. Freeze the following
immutable facts for PR #77 composition:

- RESEARCH HEAD `bbca84c6f4280ece7476187d7f4dbabaf6d63542`;
  reviewed file blob `402c25410ba4cc6704911148b1ec0c5bba720088`.
- DATA contemporary HEAD `e326a5cc92fc3ecbe48bee358eebb8c0ded81173`;
  reviewed file blob `6fdaad02ee754d4aef9f158d5969d49877ec6959`.
- These versions differ in explanatory comments and server-side address
  expression, not in the core guard requirements. Both test explicit
  `conn.info.host`, `hostaddr`, `port`, `dbname`; resist ambient
  `PGHOSTADDR`, `PGHOST`, `PGPORT`, `PGDATABASE`; retain
  `test_research_dsn_refuses_explicit_hostaddr_redirect` for both
  `127.0.0.2` and Docker `172.18.0.2`.

**Recommended compositor choice:** pin a single immutable HEAD of DATA #72
after its edits stop, copy the complete `tests/platform/test_research_postgres.py`
blob from that pin into the combined tree, verify all requirements above and
exact byte/blob SHA, and do not overwrite with an older RESEARCH copy.
All other RESEARCH-only paths should come from the exact RESEARCH PR HEAD.
Run `Pinned research PostgreSQL integration` on the **combined tree**,
including real env-redirection negative tests. Revalidate DATA SQL migration
`008_public_unknown_taker_side.sql` and the CHECK/append-only tests.
Do not claim production PG14/18 PASS based only on these PG16 tests.

Suggested deterministic integration inventory from PR #71
(relative to source `b11b79832d1b0d848773ff077a36e7cb0595fdcb`):
- `src/integration/research_ui_projection.py`: read-only trusted
  `read_verified_ui_observation` seam; no new HTTP route.
- `tests/platform/test_data_research_adapter_issue59.py`: Decimal,
  Footprint/Delta/CVD/VWAP/POC, UNKNOWN, provenance, UTC, lookahead,
  negative F/G/E and real DATA→PostgreSQL→RESEARCH readback checks.
- `src/integration/data_research_adapter.py`: reviewed DATA #72
  informational pin (not a new trade/coverage authority).
- `.github/workflows/research-issue59.yml`: isolated tests and
  immutable DATA pin, not a production deployment workflow.
- `tests/platform/test_research_postgres.py`: shared overlap,
  resolve to **one DATA-pinned blob** as specified above.
- `docs/research_ui_issue59_contract.md`: scoped integration and
  safety evidence.

The combined F-G-D browser HTTP 404 is independently traced:
`frontend/app.js:loadInstruments()` requests
`GET /api/market-catalog/linear` while
`scripts/combined_browser_ci.py` explicitly starts its API with
`market_catalog_enabled=False`. A true route/fixture fix must be
authored by the UI/combined owner, not RESEARCH; absent catalog data
must remain visibly unavailable. Combined Playwright additionally expects
`/Tester/i` in the homepage, while currently rendered heading is
`HISTORICAL LAB / BYBIT MARKET RESEARCH`. No RESEARCH code may
silence the 404 or falsely certify F→G→D.

Remaining unresolved: complete actual historical source coverage
(UNKNOWN), atomic B+execution revision/known-at, real-market F admission
DENIED, genuine hosted PG14+PG18 and full combined-browser acceptance,
and other shared CI gates. PR #77 is documentation-only and unchanged.
