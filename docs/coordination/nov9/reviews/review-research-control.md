# Nov 9 independent research/control integration audit

Scope: YehorBabenko88/tester only. Exact requested heads: PR39 **0ec0f9aebd67a811bd6254b8afbe38b5e0a742e9**, PR46 **94868a60a600831c5773153de058fcf909c6ba8d**. Reviewed exact Git archives under work/nov9/research-snapshot and market-snapshot. H was separately frozen at origin/workstream/h-trader-intelligence **8bafc315e5757b3510d1b20154b8df7445f3408c**. This PC is not ADMINIT. No PostgreSQL, app/server, browser, SCM, office connection, installation or elevation was started. No root product source, branch or staged state changed.

## Conclusion and concrete missing bridge

The genuine synthetic research vertical exists as source, but **one combined catalog/download/research application scenario is missing at these heads**. This is an integration gate, not a claim that the separately scoped producer PRs are broken.

Reproducible source evidence:

1. PR46 src/api/main.py:create_app constructs the market reader, download JobQueue, optional PersistentCatalogService and market router, then its frontend root. It does not mount research control routes or dispatch research jobs.
2. PR39 src/integration/research_runtime.py:create_app constructs create_persistent_control_app, installs only its research polling lifespan/identity, and mounts the frontend root. It does not construct the market/download/catalog app.
3. PR46 frontend/app.js calls mountStrategyWorkspace; frontend/strategy.js renders DATA_UNAVAILABLE cards, including trader-source permission UNKNOWN. index.html has no research navigation. The working actual research renderer is separate research.html/research-app.js with its own command/token form.
4. PR46 market_runtime.context explicitly rejects roots already containing capability/browser-command.json; market seed rejects existing tester_research/schema state. PR39 research seed creates those exact artifacts. Combining the existing launchers by reseeding/adopting one root therefore fails by design. Do not remove these ownership checks to obtain a green test.

The present research Command is **chart.overlay against an already published exact artifact digest**. PersistentControl.dispatch claims a lease and resolves/completes that stored artifact; it does not execute arbitrary user strategy DSL in response to the browser command. Genuine F execution happens in run_synthetic_research/run_certified_fixture and is replay-verified by publish_bundle before publication. A successful render job is not evidence that an interactive strategy-run endpoint exists.

## Actual source chain and safety contracts

| Stage | Concrete entry point | What is established / what is not |
|---|---|---|
| Genuine F/J | src.integration.strategy_lab.run_certified_fixture / run_synthetic_research | Executes pinned F998 via src.backtest.run; preserves full inputs/result/digests and explicit SYNTHETIC_ONLY/real_market_coverage NOT_PROVEN. UNKNOWN/authentic/unqualified history is not relabeled complete. |
| Publication | src.api.research_ledger.PersistentResearchLedger.publish_bundle | Calls verify_research_bundle, which genuinely reruns F and compares canonical bytes/types; stores immutable full bundle/chart in tester_research. Operator/source method, not arbitrary HTTP publication. |
| Permissions | issue_credential/authenticate/_acl | Hash-only expiring/revocable token; server-derived actor; exact artifact/action grant and binding checks. Browser actor text is not authority. Research DSN is explicit loopback/non-5432/tester_research_*, with numeric hostaddr pinned. |
| Job/replay | submit / claim / complete_leased / PersistentControl.dispatch | Actor+request identity and canonical digest distinguish replay/conflict; PostgreSQL leases fence stale workers; current auth/ACL is rechecked. No SQLite or success-returning browser fixture is the canonical ledger. |
| G transport | src.api.research_g_client.ResearchGatewayClient | Uses pinned G transport/validation and fixed research routes; preserves the full typed v2 chart rather than the old lossy overlay model. OS-owned Python invocation/four-tuple checks must remain before capability disclosure. |
| D rendering/ACK | frontend/research-app.js and research-chart.js; Control.ack / PersistentResearchLedger.acknowledge | Checks digest and exact market/run/snapshot/revision/range/cutoff, renders candles/actual fill markers and submits the complete unique fill-ID set. SQL ACK atomically checks stored digest/fill set. This is a renderer acknowledgment, not independent proof a person saw pixels. |
| H imports | src.trader_intelligence.imports.import_bytes, Store.put, service.Research | Bounded canonical CSV/JSON; USER_AUTHORIZED manifest/rights reference; tenant-scoped immutable dataset/run and source revocation. Typed callable port, not authenticated G/E HTTP integration. Manifest permission is supplied authorization evidence, not an independent identity/rights investigation. |

H rejects attributing ANONYMOUS_MARKET_PRINT to a trader. Reconstruction remains UNKNOWN when inventory/order/hedge/inverse assumptions fail; missing fees/net performance remain unknown. UI disclosures and inferred hypotheses stay separate. H's handoff explicitly says current G lacks the trader capability and E/D binding remains coordination work. PR39 does not contain H's src/trader_intelligence package. Do not manufacture trader identity from Bybit public candles/prints or infer SL/TP/rules as disclosed facts. H's Store accepts a caller-supplied DSN and trusted tenant/approval; an eventual HTTP adapter must add the reviewed DSN/auth/capability boundary, not expose those arguments directly.

## Dependency matrix

All PRs remain Draft/open/unmerged at metadata read; their body evidence is producer/coordinator-reported, not execution repeated here.

| PR | Frozen head inspected/recorded | Role / integration implication |
|---|---|---|
| [30](https://github.com/YehorBabenko88/tester/pull/30) | 53a51dab006a2d5862c4285f8383b2a23d204f07 | Reviewed market/native baseline and retained UNKNOWN coverage; not a research service activation. |
| [37](https://github.com/YehorBabenko88/tester/pull/37) | f8f49447cff2dfc713cfddc03dd473506c31b29e | J genuine F998 adapter/full immutable bundle, synthetic admission and packaging. Integration's embedded J pin can differ from current producer head. |
| [38](https://github.com/YehorBabenko88/tester/pull/38) | c27c968b8499ac2669e3a647c941206bb9fdfde3 | K PostgreSQL auth/jobs/artifacts/ACK and pinned G; optional separate control factory, no main mounting/runtime DDL. |
| [39](https://github.com/YehorBabenko88/tester/pull/39) | 0ec0f9aebd67a811bd6254b8afbe38b5e0a742e9 | Composed standalone synthetic research source; last head tightens exact Python argv ownership. PR body executable/evidence SHAs are older named artifacts, not this head's new execution proof. |
| [45](https://github.com/YehorBabenko88/tester/pull/45) | 6a703b1c1e6b106a268720799606647d01528f53 | Documentation-only default-off additive native plan, frozen on A43. Explicitly prohibits replacing main with research_runtime, runtime DDL, implicit market-DSN reuse and unreviewed activation. No implementation authority comes from the plan. |
| [46](https://github.com/YehorBabenko88/tester/pull/46) | 94868a60a600831c5773153de058fcf909c6ba8d | Unified market catalog/download UI and durable catalog source; separate research files are present but not composed into main. Exact runtime_identity.py and research_runtime.py match requested PR39 security head in Git diff. |

PR39 IL_PRODUCER_PINS records F998c5bc, G5edb722, Jd7b48ef, K6bceca6 and L7659674. Those are integration pins, not permission to assume all later PR37/38 changes were merged. Review the exact composed tree and artifacts, not a sum of producer test counts.

## Smallest safe composition/failing regression

ROOT should own one explicit **default-off additive factory** built around src.api.main.create_app(settings, provider). Supply separately validated, pre-provisioned Research DSN/roles and create_persistent_control(dsn); attach its authenticated research routes and a bounded ResearchDispatcher. Reuse genuine operator/source F publication and exact artifact grants. Do not invent a strategy-run success API merely to satisfy render ACK. Real-market selection remains NOT_READY when source coverage is UNKNOWN.

Concrete router/lifecycle hazards:

- main.create_app already appends StaticFiles('/') before returning. Naively calling app.include_router(research_app.router) afterward puts research routes behind the catch-all mount; they return static 404 rather than reaching authenticated handlers. Mount/insert reviewed API routes before the root static mount, without shadowing existing market/download/identity paths.
- include_router does not copy the separate app's middleware or lifespan. Research create_control_app denies cross-origin/cross-site GET as well as writes; main local_writes only checks writes and uses its typed loopback port set. Preserve the stronger research predicate explicitly. Do not widen Host/Origin/CORS or weaken process/image/argv/accepted-four-tuple ownership to fix test failures.
- Compose catalog startup/shutdown and ResearchDispatcher with AsyncExitStack or an equivalent reviewed lifecycle. Assigning another lifespan silently discards the catalog task or research task. Failures/drain/credential expiry must surface honestly; no hidden worker process or cancellation-as-success.
- Default off must make no research DB connection/task/DDL. Enabled-but-unprovisioned must fail explicitly. Do not call research_runtime.seed, research_lab.prepare or market_runtime.seed during app construction. Their existing fresh-root refusals stay intact.
- UI selection context and artifact binding must agree. A BTC synthetic fixture cannot silently represent selected 0GUSDT or another timeframe. On context change clear/rebind the overlay; retain exact source/run/snapshot/digest and synthetic-only labels.

First regression should assert route order, both lifecycle handlers, default-off zero research connections, configured enabled identity, preserved Host/Origin/auth rejection, no runtime migrations and refusal of cross-symbol artifact/cursor. These source/unit seams do not establish E2E.

Then ROOT's hosted-CI unmocked scenario should use an explicit test-bound synthetic catalog/source adapter (no Bybit network), actual canonical queue/worker/ingestion and actual PostgreSQL research ledger. One browser flow must: select catalog row → enqueue/download fixture → observe Download COMPLETE separately from Coverage UNKNOWN → show that those records cannot admit an authentic F run → explicitly choose the qualified synthetic F scenario → execute/replay/publish genuine F → submit actual G-authenticated job → render D exact candles/fills → exact ACK persisted. Replay, changed payload, invalid/revoked/expired token, wrong digest, omitted/duplicate fill IDs and cross-actor access must reject without mutating existing artifacts/jobs. Test-only injected source returns source data, not canned successful job/results; browser response routing is not mocked. No H/trader claim is needed for this synthetic positive scenario.

H needs a later separate authorized-export flow: trusted tenant + explicit rights/immutable import revision → typed approved plan → inferred analysis → correctly labeled overlay/digest ACK, with revoked-source and anonymous-print negatives. Do not broaden G's chart-only capability to arbitrary trader/NL commands as part of the minimal composition.

## Verification actually executed here

Exact PR39 snapshot pure command, with no disposable DB environment or server/browser launch:

```powershell
# cwd: work/nov9/research-snapshot; existing interpreter, no install
../../venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/integration/test_strategy_lab.py tests/platform/test_research_control.py tests/platform/test_runtime_identity.py -k 'not test_api_auth_origin_host_typed_input_and_unavailable and not test_expired_or_revoked_credential_is_rejected_on_each_api_call'
```

**67 PASS, 2 deselected**, one import-time TestClient deprecation warning. The two in-process HTTP-app tests were deliberately excluded under this PC's source/pure-only boundary. These passes include genuine F computation/replay/admission and fake-ledger control negatives, not PostgreSQL or HTTP evidence. Log: work/nov9/research-pure-tests.log.

Exact H snapshot pure commands:

```powershell
# cwd: work/nov9/h-snapshot
../../venv/Scripts/python.exe -m unittest discover -s tests/trader_intelligence -p test_core.py -v
../../venv/Scripts/python.exe -m unittest discover -s tests/trader_intelligence -p test_research.py -v
```

**10 + 18 PASS**: disclosed/inferred/anonymous boundaries, bounded import/rights rejection, revision/as-of handling and conservative reconstruction. Log: work/nov9/h-pure-tests.log. H PostgreSQL/tenant/auth/ACK were source-reviewed only.

Do not run TEST_RESEARCH_DATABASE_URL tests, research_runtime/research_lab/market_runtime CLI, npm test:research:e2e or browser/worker/server fixtures on this PC under the current instruction. They are future explicitly isolated hosted-CI/authorized-environment work. Prior ADMINIT PG/browser/physical metrics and PR CI success remain attributed external evidence. This report issues no new DB/E2E/deployment success or native activation approval.
