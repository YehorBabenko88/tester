# UI #74 → MASTER #77: exact-source and synthetic E2E replay

**Only for disposable CI/test environments.** Never run against PC4, main, production PostgreSQL 5432 or real exchange accounts. These are reproducible procedures, NOT results from a new merged candidate.

## Reviewed dependencies and transfer

- Baseline PC4 source: 3b2eef8a4139195db1a775545e83f1c1f58bf403.
- PR #70: 6240b6665fb3125c06eb91ef535b96700988a7ab. Apply its ancestry before UI #74. Both inherited frontend/capabilities.js and frontend/tests/capabilities.test.js must be the *updated #74 versions*, not overlaid by older #70 files.
- Full UI list: docs/integration/ui74-master77-manifest.json (35 paths, 11 runtime, 19 tests/probes, 5 docs/CI). Compare to live GitHub PR #74 changed-file list; do not infer files from a partial diff.
- DATA #72: d5bfdad445a1eefb9dc12bf2725a00f171e2c321, execution_contract.py blob 8dc34f5fc73c4d9a1199efb8081be63ecdbf1d79. Read-only original HTTP/page/row proof and Unknown-side migration 008.
- RESEARCH #71: c01c32b03aaf04923b33b0513b71776b12af1e09; data_research_adapter.py blob 35404adef064a667faef6a8f3f852f5aa492cf76, research_metrics.py blob d565c1239b60e770c0337423c5e014daeec0e457, research_ui_projection.py blob 1c165bccaa39fcf73cf33171730e9ea7d7fd6474. The trusted read_verified_ui_observation() performs original persisted source readback. Its http_route is NULL/NOT_REGISTERED: it is not an approved HTTP analytics endpoint.
- G/E: explicitly composed Research control ASGI app with server-issued bearer ACL and digest-bound acknowledgments. No real trading/F admission.

## Route and panel gates

| UI | Backend dependency | No-source behavior |
|---|---|---|
| Discovery | GET /api/capabilities → tester.ui-capabilities/1 | UNVERIFIED, no invented resource |
| Mark / Index | GET /api/price-candles 1.1 with explicit ReferencePriceReader; original Decimal OHLC, UTC closed bar, Bybit provenance, no volume invention | reference_price_route_not_enabled or reference_price_source_not_connected |
| Footprint, known Delta, window CVD, VWAP, POC | DATA read_execution_events → RESEARCH read_data_metrics → read_verified_ui_observation | endpoint_contract_not_supported; do not request /api/analytics |
| OI, Funding | DATA source-native public auxiliary, separate original HTTP-proven read service | UNVERIFIED/source_not_connected; pure no-fetch adapters only |
| Profile | No qualified profile route | endpoint_contract_not_supported |
| Catalog | /api/market-catalog/{category} only when market_catalog_enabled and router actually registered | Do not request when disabled |
| Synthetic G/E | POST /api/control/research, GET result, POST /ack, authorized actor + artifact digest/marker IDs | 401/403/409 denial; no implicit access |

Unknown coverage, publication, historical known-at and dataset revision must never be upgraded to COMPLETE by a bounded sample.

## FastAPI + Chromium (13 UTC timeframes) from an immutable composed checkout

Use an isolated GitHub-hosted Ubuntu 24.04 runner with Python 3.11 and Node 22. EXPECTED_COMBINED_SHA must be set to the actual independently reviewed combined commit. These commands run the *synthetic price fixture*, not live Bybit:

~~~bash
set -euo pipefail
test "$(git rev-parse HEAD)" = "$EXPECTED_COMBINED_SHA"
python -m pip install -e '.[dev]' -c requirements-platform.lock
python scripts/diagnose_ui_contract_drift.py > capability-contract-drift.json
python scripts/compare_download_kind_proposal.py > capability-kind-proposal-evidence.json
python -m pytest tests/platform/test_download_kind_proposal.py -q
python -m pytest tests/platform/test_capability_discovery.py tests/platform/test_ui_source_protocol.py tests/platform/test_capability_data_bridge.py tests/platform/test_ui_composition_manifest.py -q
(cd frontend && npm ci --ignore-scripts && npm test && npx playwright install --with-deps chromium)
python scripts/capability_fixture_server.py > capability-backend.log 2>&1 &
api=$!
trap 'kill -TERM "$api" 2>/dev/null || true; wait "$api" || true' EXIT
for i in $(seq 1 30); do
  if curl -fsS 'http://127.0.0.1:18874/api/capabilities?category=linear&symbol=BTCUSDT&interval=1&start_ms=0&end_ms=60000' >/dev/null; then break; fi
  sleep 1
done
curl -fsS 'http://127.0.0.1:18874/api/capabilities?category=linear&symbol=BTCUSDT&interval=1&start_ms=0&end_ms=60000' >/dev/null
(cd frontend && npx playwright test --config playwright.capability.config.js)
~~~

Reproduce the exact source checkout checks from .github/workflows/capability-browser.yml by checking both pinned Git HEADs, Git blob SHA, then running:

~~~bash
python scripts/verify_ui_source_contracts.py \
  --data .ci-source-data72/src/ingest/execution_contract.py \
  --research .ci-source-research71/src/integration/data_research_adapter.py \
  --metrics .ci-source-research71/src/analytics/research_metrics.py \
  --research-ui .ci-source-research71/src/integration/research_ui_projection.py
~~~

That AST audit proves interface compatibility, not a source-backed market-data runtime.

## Combined synthetic F-G-D + PostgreSQL 16

Authoritative provisioning: .github/workflows/combined-synthetic-browser.yml. The backend script requires GITHUB_ACTIONS=true supplied by GitHub Actions itself, TESTER_COMBINED_CI=synthetic-disposable and explicit disposable PostgreSQL endpoint 127.0.0.1:55472 / tester_research_combined_ci. Do NOT fake the GitHub environment variable on a production host. A *fresh* runner supplies an isolated PostgreSQL 16 service; the published 55472 port must never point at existing PostgreSQL 5432.

~~~bash
set -euo pipefail
test "$(git rev-parse HEAD)" = "$EXPECTED_COMBINED_SHA"
export TESTER_COMBINED_CI=synthetic-disposable
export TEST_RESEARCH_DATABASE_URL='postgresql://tester_ci:disposable-ci-only@127.0.0.1:55472/tester_research_combined_ci'
export TESTER_COMBINED_FIXTURE_FILE="$RUNNER_TEMP/combined-private.json"
python -m pip install -e '.[dev]' -c requirements-platform.lock
(cd frontend && npm ci && npx playwright install --with-deps chromium)
python -m pytest tests/platform/test_combined_browser_fixture.py -q
python -m scripts.combined_browser_ci prepare "$TESTER_COMBINED_FIXTURE_FILE"
python -m scripts.combined_browser_ci serve "$TESTER_COMBINED_FIXTURE_FILE" >combined-server.log 2>&1 &
api=$!
trap 'kill -TERM "$api" 2>/dev/null || true; wait "$api" || true' EXIT
python -m scripts.combined_browser_ci gateway "$TESTER_COMBINED_FIXTURE_FILE"
(cd frontend && npx playwright test -c playwright.combined.config.js --reporter=line)
python -m scripts.combined_browser_ci verify "$TESTER_COMBINED_FIXTURE_FILE"
~~~

Acceptance: 7 fixture Python, 1 real Chromium E2E, all 13 display intervals, zero unexpected 404/422/503 and catalog/analytics probes, unauthorized G/E 401, forged artifact digest ACK 409, successful actor-scoped digest-bound renderer ACK persisting exactly its fill IDs in disposable PostgreSQL. This still is SYNTHETIC ONLY and proves no live Bybit history.

## Cross-owner blockers

- DATA #72 and RESEARCH #71 both changed tests/platform/test_research_postgres.py. Combine their negative PGHOSTADDR cases: explicit conn.info.host/hostaddr/port/dbname must match validated client DSN despite hostile PGHOSTADDR/PGHOST/PGPORT/PGDATABASE; explicitly specified DSN hostaddr must be rejected. PostgreSQL server inet_server_addr() is container-internal and must not equal runner published address.
- OpenAPI Kind mark/index drift belongs to owner Issue #76. Keep contracts/v1/openapi.json and old clients untouched. Proposal docs/proposals/download-kind-v1_1.json is inactive and versioned; neither a new download route nor permission to fetch bulk Mark/Index has been granted.
- No approved HTTP read route for observed RESEARCH/OI/Funding yet. Pure source-to-chart adapters must stay no-fetch and return UNAVAILABLE/UNVERIFIED.
- MASTER PR #77 must be composed and tested at one exact immutable combined HEAD. No UI target PASS can authorize real historical F or change Bybit 403 ACCESS_BLOCKED.
