# F / G / H integration preparation - 2026-11-08

Status: **NOT_INTEGRATED** for Strategy Lab (F), Codex Control Plane (G), and Trader Intelligence (H) in the current A-E application. This document prepares integration; it does not enable an endpoint, bind a provider, install a service, run a real-data strategy, or submit orders. Existing `frontend/strategy.js` intentionally displays DATA_UNAVAILABLE until validated outputs are connected.

## Source scope and verified pins

Only this repository, `YehorBabenko88/tester`, was inspected. These are exact locally available remote-tracking refs resolved with `git for-each-ref` and read with `git show`; they were not refreshed against GitHub during this preparation. They identify verified local Git objects, **not confirmed current remote heads**. Freeze and recheck the producer/consumer heads in the integration PR before adoption.

| Component | Local remote-tracking ref | Exact inspected commit | Present implementation / status |
|---|---|---|---|
| F | origin/workstream/f-strategy | `998c5bc89d1b35c9ad3047b1e50dcb1295120e31` | Offline DSL, engine `strategy-bar-1.1.0`, immutable JSON research artifacts; NOT_INTEGRATED |
| G | origin/workstream/g-control-plane | `5edb722fc936a56e81a174cfe654839de9a19a9d` | Typed local command dispatcher, scoped policy, approval/job journal and optional MCP client bridge; NOT_INTEGRATED |
| H | origin/workstream/h-trader-intelligence | `8bafc315e5757b3510d1b20154b8df7445f3408c` | Authorized canonical export research, PostgreSQL snapshot/run store, callable research port; NOT_INTEGRATED |
| Integration baseline at preparation start | origin/integration/adminit-acceptance | `4c82494d671c4e43bc1ef4d5cda63493e42fe407` | A-E application and fail-closed Strategy workspace; pending changes require their own candidate SHA |

Normative boundaries remain [STANDALONE](../architecture/STANDALONE.md), [integration plan](../coordination/INTEGRATION_PLAN_V1.md), [composed components](../integration/COMPOSED_COMPONENTS.md), and [Trader Intelligence sequencing](../trader-intelligence/INTEGRATION_PLAN.md). Earlier source reviews are historical evidence; notably F's old `c72cf505...` defects were corrected in the inspected F branch and are not asserted to remain open.

## Existing pieces and ownership map

| Boundary | Existing source at the pin | Concrete next connection | Required gate |
|---|---|---|---|
| A/B/C to F | F `src/backtest/quality.py`, `engine.py`; `src/strategy/dsl.py`, `text.py`, `levels.py` | B/E build an immutable, request-bound market snapshot using actual retained source/run/provenance records; C supplies features with availability cutoffs | Exact canonical market row validation, closed-bar quality, complete required range, immutable evidence and source revisions; no download-state-to-coverage promotion |
| F persistence | `src/backtest/artifacts.py`, `research.py`; docs/strategy/CONTRACT.md and MIGRATIONS.md | B owns canonical PostgreSQL strategy/validation/snapshot/run/fill/artifact metadata; portable JSON remains an export | Transactional immutable IDs, replay hashes, retention and foreign-key ownership reviewed by B/E |
| F to E | F engine and preview/digest functions are callable; API paths in its CONTRACT are proposed interfaces | E job adapter validates/authorizes a stored preview, pinned snapshot and frozen assumptions, schedules bounded offline work, persists outputs | Real authentication, explicit approval record, idempotency, cancellation/checkpoint/restart, resource limits; no arbitrary Python or automatic ambiguous prose execution |
| G to E | `src/control/api.py`, `plane.py`, `contracts.py`, `policy.py`, `commands.py`, `bridge.py` | E exposes authenticated tester-only typed control routes and injects owned domain providers; Codex remains an optional client | Provider origin policy, Host/Origin/CSRF, action/object ACL, expiring capability, approval revocation before dispatch, actual supported client transport |
| G authoritative state | G durable SQLite plans/jobs/config/audit | Decide a canonical PostgreSQL control schema and migration/repository adapter before product wiring | No second authoritative ledger; SQLite may be staging/cache only. Existing data is retained; migration needs reconciliation and explicit ownership |
| F/G to D | F result markers + G proposed bounded overlay projection; D `frontend/strategy.js` placeholders and chart coordinates | New versioned read-only projection plus D marker/level renderer | Preserve uncertainty and run/snapshot provenance, exact fill correspondence, stable cursors and market/range/revision binding; see mismatch below |
| H to B/E | `src/trader_intelligence/imports.py`, `store.py`, `schema.sql`, `service.py` (`Research.plan/run/events/read_run`) | B reviews proposed tester_trader persistence; E binds authenticated tenant identity and durable jobs | Rights/consent/revocation, schema/retention ownership, bounded immutable original-byte evidence, trustworthy event/publication/observation cutoffs |
| H to F/G/D | H hypotheses/reconstruction, typed plan digest and research output | Read-only analysis first; a hypothesis can become a separately approved frozen F DSL run, never an observed strategy fact | G currently lacks the trader capability; add reviewed schema/action scope. D distinguishes DISCLOSED, INFERRED and SIMULATED and records render ACK tied to artifact digest/revision |

The current `src/api/control.py` only drains/resumes the existing download queue. Its filename is not evidence that G's control API is mounted. No F/G/H packages were copied into the application by this task.

## Concrete contract mismatch blocking a minimal overlay adapter

A genuine F implementation and synthetic output exist. At F's inspected pin, fills and corresponding overlay markers retain `known_at_ms`, `time_window: {start_ms,end_ms}`, and `time_precision` (`bar` or `modeled_open`), plus stable fill/order IDs. Whole-bar liquidity may create a bar-uncertain fill known only at close. F's `example.result.json` is synthetic evidence, not a real B market run.

G's inspected `validate_overlay` admits a strict marker shape `{timestamp_ms,kind,price,trade_id,fill_id,reason}`. It has no uncertainty window/knowledge fields and requires trade_id for every fill. F entry fills can belong to a still-open position without a closed trade record. Stripping the timing fields, assigning an invented closed-trade ID, or presenting bar-end surrogate timestamps as exact executions would be incorrect.

**Safe next adapter:** a pure, versioned F-result-to-overlay projection, implemented only after F/G/E agree an uncertainty-preserving schema and ID mapping. It should accept already validated immutable F artifacts, resolve fill/order/position/closed-trade relationships without invention, preserve timing knowledge, and produce bounded typed primitives. It needs no network, SQL, engine rewrite or live-order client. No such adapter was implemented in this preparation because the schema decision and actual canonical snapshot gate are unresolved.

Minimum adapter rejection fixtures: artifact/input hash mismatch; wrong run/market/range/revision; lost or fabricated fill; duplicate marker/fill; entry with no closed trade; bar uncertainty discarded; known_at beyond view cutoff; cross-snapshot cursor; partial coverage upgraded; source refs unresolved. Passing synthetic fixtures does not establish real market completeness.

## Proposed simulation output contract to review

This is a **proposal**, not an existing approved API contract. Preserve F's artifacts rather than silently changing their meaning.

- Identity: schema/projection version, run_id, strategy_id/version/hash, engine_version, engine code commit, immutable artifact digest, market_snapshot_id/hash/revision, venue/category/symbol/interval, requested UTC `[start_ms,end_ms)` and information cutoff.
- Admission: actual actor-scoped validation and preview approval IDs/digests; resolved fees, spread/slippage, funding, liquidity, margin/liquidation, gaps, seed and supported instrument/timeframe assumptions. Approval is persisted authorization, not a caller-supplied boolean.
- Evidence: original source IDs, ingestion run IDs and source response hashes resolved through B, source/data revisions, point-in-time available_at, availability and coverage as separate fields, warnings and synthetic/exploratory flags. A nonempty evidence reference or digest is not independent source certification.
- Execution: `execution_mode: SIMULATED`; immutable orders, fills, position links and any closed-trade links; Decimal-string prices/quantities/costs; event timestamp, known_at, precision and uncertainty window retained. No exchange order ID, observed execution assertion or real-order submission.
- Analytics: equity/drawdown/metrics with unknown costs/unsupported statistics explicit; checkpoint/replay digest; bounded result/artifact manifests and authorized digest-checked retrieval.
- Projection: typed bounded marker/level pages with source run/fill/order/position IDs; fixed market/run/revision/range/cutoff cursor scope; no HTML, JS or arbitrary file paths. Every rendered simulated fill must match one persisted fill and all accepted in-view fills must be accounted for across pages.

B/E own canonical persistence and API authorization. F owns financial simulation semantics. D owns presentation and coordinate transforms. G orchestrates validated IDs under capabilities; H supplies separately classified authorized disclosures/hypotheses. None gains direct SQL/shell/real-order authority from a chart click.

## Chart coordinates and simulation presentation

Use the existing chart's shared UTC time domain and price domain: `frontend/charts.js` `plotGeometry` time mapping and panel price transform; do not position markers by array index or DOM pixel guesses. Keep exact Decimal strings for labels/tooltips and original artifact comparison; numerical canvas coordinates are presentation only. Clip to the requested half-open range, recompute on pan/zoom/resize/high DPI, synchronize the crosshair, and retain market/timeframe/run/revision while paging.

Always show **SIMULATED** on the simulation panel and trade tooltips, independently of market coverage. Differentiate signal, simulated entry/exit, protective SL/TP level and disclosed/inferred H event by shape and text, not color alone. For a bar-uncertain fill, render its time window and explain that the precise within-bar time is unknown; known_at controls point-in-time visibility. `modeled_open` is a model assumption, not a verified exchange execution. Show order/trigger residuals separately from fills. Never create a trade marker solely because a stop level was crossed without a recorded fill.

The terminal advertises 13 display intervals (1,3,5,15,30,60,120,240,360,720,D,W,M). That does not mean F supports every interval: F explicitly rejects calendar-month candles, inverse payoff, borrowed spot and unsupported intrabar funding settlement. Admission must reject unsupported combinations rather than render a successful-looking simulation. OI native cadence and funding event timing remain separate from display timeframe.

## Acceptance and exact safe action sequence

1. Freeze/reconfirm F/G/H and application candidate SHAs; compare contracts and rerun focused producer tests. Keep all integrations NOT_INTEGRATED until downstream evidence exists.
2. Complete real-data A-E acceptance first: actual source bytes/run/provenance through PostgreSQL and API into bounded charts, matching requested market/range; inspect quality/coverage, restart and ten-instrument evidence. A COMPLETE download is not a verified market snapshot.
3. B/E decide PostgreSQL ownership of G control and F/H canonical records, including atomic job/result/audit linkage, migration/import/reconciliation and retention/revocation. Do not create parallel authoritative SQLite state or automatically apply H DDL.
4. F/G/E agree and version the uncertainty/ID-preserving projection above. Implement the pure adapter with adverse fixtures before adding endpoints or UI rendering.
5. E binds only the owned pinned providers and durable authenticated job routes. Test digest-bound preview/approval, unsupported strategy rejection, cancellation/restart/idempotency, scoped artifact retrieval and audit continuity in disposable environments.
6. D renders a pinned synthetic fixture as clearly SIMULATED to validate geometry, uncertainty windows, fill counts, cursors, keyboard/resize/high-DPI behavior and fail-closed states. Label this synthetic visual verification; it is not a real-data acceptance.
7. Run a separately approved offline strategy on an actually validated immutable B snapshot. Persist run/artifact/fill evidence, compare restart/replay outputs and inspect chart correspondence. A synthetic successful example cannot substitute for this gate.
8. H remains gated: no live public trader acquisition until actual source-specific access/consent/rights and bounded authentic sample are recorded. Anonymous market prints cannot identify traders. Start with authorized canonical exports; preserve UNKNOWN costs, disclosure lag and inferred-hypothesis labels. Add G trader actions only through reviewed typed capabilities.

No cross-repository service bindings, main merge, live installed-root changes, service registration, credential operations, live trader acquisition or real orders are part of this preparation.

## Verification performed and limits

On 2026-11-08, exact F Git archive `998c5bc...` (src/strategy, src/backtest, tests/strategy and docs/strategy) was extracted into task-local `work/f-preparation-998c5bc`. Running CPython 3.14 `python -m unittest discover -s tests/strategy -v` **from that extracted source root** passed all **39 tests**. This includes zero-capacity protective persistence, future-capacity timing/uncertainty, deterministic replay, artifact integrity and fill/overlay correspondence. An initial invocation from the application checkout failed source imports; rerunning from the extracted root resolved the invocation error without source changes. No network or database was used.

G/H implementation, proposed contracts and their committed reports were inspected at the exact pins above. Their reported test counts are historical producer evidence, not freshly executed acceptance here. No G transport, H PostgreSQL/UI, current remote head, real market snapshot, simulation chart renderer or installed runtime integration was verified by this document. The current A-E Strategy workspace remains intentionally DATA_UNAVAILABLE.

## Remote pin refresh during native follow-up

On2026-11-08, root ran `git ls-remote origin` for these three exact producer branches and confirmed the remote tips equal the F/G/H commits listed above. This refresh changes pin freshness only; integration and real-data simulation gates remain unfulfilled.
