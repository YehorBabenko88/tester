# UI #74 → MASTER #77: exact-source and synthetic E2E replay

**Only for disposable CI/test environments.** Never run against PC4, main, production PostgreSQL 5432 or real exchange accounts. These are reproducible procedures, NOT results from a new merged candidate.

## Reviewed dependencies and transfer

- Baseline PC4 source: 3b2eef8a4139195db1a775545e83f1c1f58bf403.
- PR #70: 6240b6665fb3125c06eb91ef535b96700988a7ab. Apply its ancestry before UI #74. Both inherited frontend/capabilities.js and frontend/tests/capabilities.test.js must be the *updated #74 versions*, not overlaid by older #70 files.
- Full UI list: docs/integration/ui74-master77-manifest.json (36 paths, 11 runtime, 19 tests/probes, 6 docs/CI). Compare to live GitHub PR #74 changed-file list; do not infer files from a partial diff.
- DATA #72: d5bfdad445a1eefb9dc12bf2725a00f171e2c321, execution_contract.py blob 8dc34f5fc73c4d9a1199efb8081be63ecdbf1d79. Read-only original HTTP/page/row proof and Unknown-side migration 008.
- RESEARCH #71: 7ec35810434e3c9159f36712a4186595ac637efa; data_research_adapter.py blob 35404adef064a667faef6a8f3f852f5aa492cf76, research_metrics.py blob d565c1239b60e770c0337423c5e014daeec0e457, research_ui_projection.py blob 1c165bccaa39fcf73cf33171730e9ea7d7fd6474. The trusted read_verified_ui_observation() performs original persisted source readback. Its http_route is NULL/NOT_REGISTERED: it is not an approved HTTP analytics endpoint.
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
  --research-ui .ci-source-research71/src/integration/research_ui_projection.py \
  --data-pg-test .ci-source-data72/tests/platform/test_research_postgres.py \
  --research-pg-test .ci-source-research71/tests/platform/test_research_postgres.py
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

- DATA #72 and RESEARCH #71 both changed tests/platform/test_research_postgres.py, but their current exact heads have the **same** Git blob SHA `402c25410ba4cc6704911148b1ec0c5bba720088`. Reuse it once in MASTER composition. The UI source-preflight CI checks both exact blob hashes and `cmp` equality. This regression verifies explicit conn.info.host/hostaddr/port/dbname against the validated client DSN despite hostile PGHOSTADDR/PGHOST/PGPORT/PGDATABASE, and rejects explicitly configured DSN hostaddr. PostgreSQL server inet_server_addr() is container-internal and must not be compared to the runner-published endpoint.
- OpenAPI Kind mark/index drift belongs to owner Issue #76. Keep contracts/v1/openapi.json and old clients untouched. Proposal docs/proposals/download-kind-v1_1.json is inactive and versioned; neither a new download route nor permission to fetch bulk Mark/Index has been granted.
- No approved HTTP read route for observed RESEARCH/OI/Funding yet. Pure source-to-chart adapters must stay no-fetch and return UNAVAILABLE/UNVERIFIED.
- MASTER PR #77 must be composed and tested at one exact immutable combined HEAD. No UI target PASS can authorize real historical F or change Bybit 403 ACCESS_BLOCKED.


## 2026-11-14 MASTER 08:27 — READY_FOR_COMPOSITION source handoff

The source-file collision is resolved for the reviewed DATA #72 head
`d5bfdad445a1eefb9dc12bf2725a00f171e2c321` and RESEARCH #71 head
`7ec35810434e3c9159f36712a4186595ac637efa`:
`tests/platform/test_research_postgres.py` has identical exact Git blob
`402c25410ba4cc6704911148b1ec0c5bba720088`. The added UI source-audit
function `verify_shared_pg_hostaddr_test` checks *both* files' bytes, Git
blob SHA and required PGHOSTADDR/PGHOST/PGPORT/PGDATABASE negative controls.
A mismatched or weakened file fails rather than allowing last-writer-wins.
This is a source-level review, **not** a substitute for real PostgreSQL
PG14/18 acceptance on the later combined SHA.

The full UI PR74 36-file list includes both updated files inherited from
PR70. Retain PR70 ancestry before copying the final PR74 content.
`contracts/v1/openapi.json` is **byte-for-byte unchanged** from the pinned
PC4 ancestor, exact Git blob `fc3f4b20d4bd6d6fed642f9e00ceb099c68c4398`.
`/api/v1_1/downloads` remains an **inactive proposal** under Issue #76;
the working `/api/price-candles` Mark/Index price-only route is a separately
qualified v1.1 read contract, not approval of downloads. No existing v1
OpenAPI is rewritten.

UI can be transferred as source for a **new immutable combined candidate**
(`READY_FOR_COMPOSITION`) only; full release, PC4 installation, real F
qualification and historical completeness remain `NO_GO`. MASTER #77 is
still documentation-only. Run the FastAPI+Chromium and Combined F-G-D
scripts above against the **same actual combined commit SHA** after the
DATA+RESEARCH PGHOSTADDR and source-file verification. Never substitute
existing successful producer CI for combined-source acceptance.


## Actual composed-source Chromium workflow and transport truth

The UI Draft branch now also defines [`.github/workflows/ui74-master-composed-e2e.yml`](../../.github/workflows/ui74-master-composed-e2e.yml). It checks out **MASTER staging SHA `4e64ec9f6a1da453019b68e598e235036701739f`**, fetches its five exact source pins, runs the actual `master_disposable_compose.py` in the ephemeral runner and confirms exactly **125** unique composed source changes including **36/36 UI files from pinned PR #74 `a61a2c3b`**. It then runs the existing real FastAPI/Chromium 13-timeframe and synthetic PG16 G/E browser cases **against this composed working tree**. The workflow does not commit/merge the ephemeral composition or modify MASTER #77.

| Capability | Real FastAPI HTTP route | Qualification / test boundary |
| --- | --- | --- |
| `/api/capabilities` | **YES** | Actual route; exposes feature negotiation and source-contract metadata; not itself historical proof |
| `/api/price-candles` Mark/Index | **YES, explicitly opt-in v1.1** | Actual FastAPI+Chromium route with original Decimal strings, UTC and provenance shape; isolated CI reader is deterministic synthetic, **not live Bybit data** |
| `/api/control/research`, result, ACK | **YES, only after authorized research composition** | Real PG16-backed HTTP and digest-bound G/E ACK in CI from certified **synthetic** F fixture; no real market admission |
| Footprint, known Delta, CVD, VWAP, POC | **NO qualified HTTP route** | RESEARCH `read_verified_ui_observation` is real **read-only PostgreSQL source-backed function**, confirmed in separate composed RESEARCH CI; its `http_route=null` and `NOT_REGISTERED`, so browser retains `UNAVAILABLE/UNVERIFIED` and does not probe `/api/analytics` |
| OI / Funding | Generic `GET /api/open-interest` and `GET /api/funding` **are registered**, but no qualified DATA #72 source reader is composed into them | Source-native DATA public auxiliary ingestion/PG proof is independently tested; UI's `projectObservedAuxiliary` is a pure **no-fetch** adapter, not source verification or an API admission grant |
| Market catalog and sources | Optional routes exist only with configured catalog/provider | Default combined test disables catalog; browser does **not** call unmounted catalog/source-list endpoints |

The 13 periods are `1,3,5,15,30,60,120,240,360,720,D,W,M`. A `PARTIAL` bounded price-only page keeps `historical_coverage=UNKNOWN`, `dataset_revision=UNPINNED` and `admitted_to_f=false`; it is not a whole-history confirmation. Footprint/known Delta/CVD/VWAP/POC source visibility and maker/taker Unknown fields remain unknown unless verified through the trusted readback; no fabricated Bid/Ask, F authority or side assignment.

**Acceptance states must remain separate:** source composition and AST syntax PASS, composed RESEARCH PostgreSQL source-backed readback PASS, and composed UI FastAPI+Chromium/PG16 G/E browser must each have **independent exact-workflow execution evidence**. No v1 `DownloadRequest.kind` rewrite is authorized by Issue #76, and no current live Bybit HTTP 403 may be relabeled as market-history PASS.

## Private generation UI74 prototype on actual four-producer pinned source (2026-11-14)

The opt-in GitHub Actions workflow
[`ui74-private-generation-e2e.yml`](../../.github/workflows/ui74-private-generation-e2e.yml)
uses the independently reviewed **OPS #73 prototype**
`scripts/ops_private_generation_prototype.py` at immutable commit
`113d7f3b97f47c86a19dc8590bdbb9809138c4d8`,
Git blob `1a28603452efaaf9d69e4446b5ee2bf8bf7a5d9e`.
It is deliberately **separate** from the MASTER #77 checkout composer
and never automatically activates its private generation.

Source pin tuple:

- BASE `3b2eef8a4139195db1a775545e83f1c1f58bf403`
- MASTER staging `5ee448fa6e0c356034e33b6f54f2ddbf6bdbc1cd`
- DATA `d5bfdad445a1eefb9dc12bf2725a00f171e2c321`
- RESEARCH `7ec35810434e3c9159f36712a4186595ac637efa`
- UI `a61a2c3bceec8747d7b2424d91c9a4a6c3ec9d96`
- OPS source `0d4649f3a7cd3595ea58757f2abaf2f1b0558667`

The runner retrieves original Git blob **bytes**, checks Git object hashes,
file modes, duplicate-path identity, forbids removed/protected paths and
passes a canonical one-to-one reviewed-blob manifest into OPS's
`publish_generation`. It requires a new private `0700` folder and checks
`CURRENT`, `generation.json`, every sealed source file and the pinned
tree SHA256 both **before and after** the browser tests. The source
checkout is not modified by this private publication; the OPS publisher
and the browser test driver are separately pinned inputs.

### Actual verified CI-only publication

[GitHub run 43596116665](https://github.com/YehorBabenko88/tester/actions/runs/43596116665)
**SUCCESS**: 492 selected source/asset/test files privately published,
124 Node tests PASS, actual FastAPI imported from the private generation,
13/13 timeframes PASS, 1/1 real FastAPI+Chromium price-only E2E PASS,
and 1/1 real PostgreSQL16+FastAPI+Chromium synthetic digest-bound G/E ACK PASS.
The full private tree SHA256 was
`e4aa57ab56c93475842b2f12e880be29c15dcde14665f17803343c97f81dfb6f`;
canonical reviewed manifest SHA256
`a67412cb5e95b7bcababa842420e219938d0300b9414991fbf0b301c155967c7`.
No private source file changed after the tests. The CI artifact contains
the generation receipt, the 492-entry manifest and source pin audit;
the follow-up workflow revision corrects screenshot/network artifact
upload paths.

**This is NOT a full MASTER committed combined SHA.** No combined Git
commit was created, and MASTER #77 did not adopt this prototype. The
reviewed producer allowlist at `5ee448fa` differs from the producer diffs
(all DATA, RESEARCH, UI, OPS allowlist parity flags false), so the MASTER
checkout composer does **not** independently certify this source set.
The private prototype uses a separate audited direct-Git-object input;
external controller authority, trusted Git/runner identity, hostile
same-UID process isolation, parent-directory relocation, crash
durability of `CURRENT` after a failed fsync and concurrent reader leases
remain **BLOCKED**. The prototype admits only paths under `src`,
`scripts`, `frontend`, `tests` and selected `docs`—the existing
frozen `contracts/v1` dependency remains a separately pinned read-only
source outside the generation. No full standalone executable package,
native launcher or production deployment is claimed.

### Capability truth when serving the sealed generation

| Component | HTTP implementation tested | Data qualification |
| --- | --- | --- |
| `GET /api/capabilities` | YES; 13/13 FastAPI and Chromium | Negotiation and exact scope only, never history certification |
| Mark/Index `GET /api/price-candles` | YES with opt-in synthetic reader | Original Decimal text, UTC bars, price-only; no fabricated volume |
| Footprint/known Delta/CVD/VWAP/POC | NO mounted source-qualified HTTP route | Pure DATA→RESEARCH verified readback adapter only; UI must show `UNAVAILABLE` |
| OI and Funding | Generic FastAPI paths registered | No authorized DATA #72 auxiliary source reader; UI remains `UNVERIFIED` and sends no unsupported request |
| G/E result and digest-bound canvas ACK | YES behind authorized `/api/control/research` in separate synthetic PG16 route | Actual HTTP/Chromium semantics, **not real market F/G/E** |
| Market catalog | Configuration-gated and disabled | No unmounted catalog requests, no accidental 404/422/503 |

The browser checks all 13 display periods:
`1,3,5,15,30,60,120,240,360,720,D,W,M`. The original page
rendering and HTTP path are tested together, but fixture source coverage
remains `UNKNOWN`, revision `UNPINNED` and `admitted_to_f=false`.
The UI file source-blob map for every changed non-self-reference file is
in `docs/integration/ui74-master77-manifest.json` under
`nonself_current_ui_file_blobs`. The separate CI proof JSON includes
per-file UI Git blob SHA, producer pin tuple, private generation ID and
source tree digest.

### Remaining acceptance

External source/manifest signing, fsync crash recovery, hostile same-UID
writer fencing, safe generation GC, genuine Bybit historical data, full
committed combined-source SHA and real PostgreSQL14/18+Chromium acceptance
of the final MASTER candidate are **NOT_RUN/BLOCKED**. Owner approval
Issue #76 is still required before changing OpenAPI v1 or implementing
versioned downloads. The private generation is **CI_ONLY / NO_GO**;
PC4, installed PostgreSQL 5432, `main` and MASTER #77 remain unchanged.
