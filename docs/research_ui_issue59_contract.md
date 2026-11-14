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

Standalone shape tests include valid exact arithmetic, unknown side,
Decimal VWAP, observed CVD, invalid and rehashed metrics, tampered
footprint/POC/UTC/digests, forged coverage/known-at/F/G/E admission and
failure to promote unauthenticated fixtures. The existing composed DATA
unit and real PostgreSQL integration cases also consume this bridge and
assert the same restrictions on actual readback.

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

MASTER also reports a separate `PGHOSTADDR` test comparison that
assumes `127.0.0.1` while the isolated GitHub postgres service uses a
`172.x` container network address. Preserve the security check against
environment redirection; review actual `conn.info.hostaddr` / pinned
socket identity before modifying that integration test. No such global
test changes are made from PR #71.

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
