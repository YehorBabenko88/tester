# RESEARCH negative coverage on MASTER disposable composition

Scope: RESEARCH Draft PR #71. **No changes to PR #77, main, PC4 or production
PostgreSQL 5432. No Codex, real orders or native launcher activation.**

## Pinning and execution contract

The combined runtime is composed **only in GitHub Actions** by
`scripts/master_disposable_compose.py` from MASTER #77 exact source
`4e64ec9f6a1da453019b68e598e235036701739f`, with DATA #72
`d5bfdad445a1eefb9dc12bf2725a00f171e2c321`, RESEARCH #71 original
pin `7ec35810434e3c9159f36712a4186595ac637efa`, UI #74
`a61a2c3bceec8747d7b2424d91c9a4a6c3ec9d96` (includes #70),
and OPS #73 `0d4649f3a7cd3595ea58757f2abaf2f1b0558667`.

The workflow independently checks bytes and versions, then fetches the **exact
invoking PR #71 HEAD** and overlays only
`tests/platform/test_research_composed_negative_issue59.py`.
It MUST NOT overlay application source from the newer RESEARCH branch or
silently modify MASTER pins. CI logs the test-only HEAD. No claimed
`MASTER #77 combined commit SHA` exists yet.

## Coverage matrix and provenance boundaries

| Boundary | Test | Expected |
|---|---|---|
| Original HTTP body SHA, base64 bytes, page hash, row hash | Parameterized DATA readback rejects malformed copies | FAIL CLOSED, not attributed |
| Missing source/provenance/evidence or parent refs | DATA v1 verifies immutable record/page/evidence lineage | FAIL CLOSED |
| Original trade price or false COMPLETE status | DATA stored content hash and original receipt checks | FAIL CLOSED |
| Source unavailable | `read_verified_ui_observation` on no verified rows | `research_ui_not_source_backed`; no UI success |
| Fabricated `persisted_dataset_revision`, `historical_known_at_ms`, or coverage | UI preview projection | `research_ui_historical_authority_refused` |
| Unknown taker side | DATA contract + RESEARCH exact sample, real PG | Preserve unknown quantity; no inferred Buy/Sell/Bid/Ask |
| Delta and window-local CVD | Source-backed Buy−Sell exact arithmetic; rehashed forged values | Exact Decimal; reject inconsistent values |
| Footprint/POC | Per-level conservation; observed volume POC and lower-price tie break | Exact; no fabricated levels |
| VWAP and Decimal | Independently recompute source price×quantity / volume via Fractions | Exact decimal string; reject NaN/negative |
| UTC and lookahead | Source observation later than requested as-of | Reject before UI/Strategy F |
| Real F on UNKNOWN | `require_strategy_lab_f_input` against actual readback | `real_historical_visibility_unknown_f_denied` |
| G/E authorization | Projected UI fields | `NOT_AUTHORIZED`, no route registered |
| PostgreSQL append-only | Corrupt *read-only copies* of actual persisted source rows | Refuse projection; raw DB remains intact |

Pure negative cases use authenticated-looking DATA v1 **fixture rows** to
exercise the actual DATA v1 verifier, not a fake metrics provider. The
separate DB integration persists **recorded HTTP response bytes** in an
isolated PostgreSQL 16 database, reopens it, computes Footprint, Delta, CVD,
VWAP and POC, and verifies bad in-memory readback copies are rejected.
No historical rows are deleted, patched or loosened in SQL.

Data integrity caveat: `project_source_observation(dict)` is only a
**shape/arithmetic verifier**. A caller may forge its dictionaries and
hashes. `read_verified_ui_observation(store, ...)` is the intended
read-only trusted entrypoint because DATA validates persisted HTTP/row/page
provenance before RESEARCH calculation. Neither entrypoint is a registered
FastAPI route, a complete-history certificate or permission to start F/G/E.

## Real test environments and remaining gates

The disposable workflow uses three **separate** PostgreSQL16 containers,
published only inside GitHub CI at ports 55476 (DATA source readback), 55477
(composed DATA E2E) and 55472 (authenticated Research Control). This prevents
known cross-test contamination of fixed historical intervals.

The workflow has distinct steps for the pure negative matrix and the real
PostgreSQL negative test, plus existing composed DATA/E2E and Research
Control PGHOSTADDR tests. A SUCCESS is valid only for the exact new PR #71
HEAD and frozen MASTER source pin shown in the workflow logs.

Still UNKNOWN/BLOCKED: genuine Bybit historical availability (403), complete
revision-coupled market history, publication known-at, real Strategy Lab F
admission, one immutable combined PR #77 runtime SHA, PG14/PG18 whole-tree
migration acceptance, native PC4 restart, and combined Chromium security
coverage. **Release NO_GO** regardless of producer test success.
