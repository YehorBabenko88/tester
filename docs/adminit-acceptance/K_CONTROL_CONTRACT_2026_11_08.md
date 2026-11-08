# Agent K — G/E read-only research contract seam (Issue #33)

Status: **NOT_INTEGRATED**. Source-only candidate on `workstream/k-codex-e2e`, based on `0b5339c357769134f32f2c2081ee9868aeee3bad`. No application route, installer, service, database or frontend is changed.

Issue: https://github.com/YehorBabenko88/tester/issues/33

## Exact source review and decision

Reviewed G `5edb722fc936a56e81a174cfe654839de9a19a9d` commands/contracts and F `998c5bc89d1b35c9ad3047b1e50dcb1295120e31` contract, alongside `F_G_H_PREPARATION_2026_11_08.md` and normative `STANDALONE.md`. No AGENTS.md was present in this assigned worktree or its checked worktree parents.

**Canonical control job/result/audit state must be PostgreSQL owned by B/E.** This change adds only the `Ledger` protocol. It does not adopt G's SQLite journal, create a second authority, add a migration, run DDL, reconcile existing user data or claim production recovery. A reviewed PostgreSQL implementation must atomically bind actor/request id, command digest, job, immutable artifact digest and audit; implement fencing/leases and restart recovery; compare ACK digest inside its transaction. The in-memory test double is explicitly synthetic and is never imported by production code.

## Implemented surface

`src/api/research_control.py` is an opt-in factory, not imported/mounted by `src.api.main`. Only `chart.overlay` is admitted: resolve an existing immutable read-only research chart artifact through a trusted injected port. It does not run a strategy, download data or execute domain code supplied by a caller. No arbitrary params, caller actor, shell, SQL or provider expression is accepted.

The opt-in harness exposes submit/status/render-ACK routes. The worker dispatch seam is deliberately not an HTTP endpoint. Authentication resolves server-side `Identity` on every request; the injected authorizer must enforce current expiring/revocable action and object capabilities on submission (including replay), dispatch, result and ACK. Authorization is rechecked after provider resolution. Host filtering allows loopback hostnames only; browser Origin headers and cross-site fetches are denied, and no cookie authentication or CORS is introduced. Product wiring must bind to loopback and supply reviewed authentication/provider ownership policy. There is no default credential or permissive default authorizer. A missing control/provider returns unavailable rather than simulated activation.

Commands pin run, snapshot, code revision, venue/category/symbol/interval, bounded half-open view, information cutoff and expected canonical artifact SHA-256. Actor-scoped idempotency keys cannot be reused with another digest. Results are revalidated against the command and both expected/stored digests on retrieval. Render ACK confirms only the exact accepted artifact digest through the ledger port; it is a client acknowledgment, not independent evidence that pixels were rendered.

The proposed `tester.research-chart/2` contract preserves exact Decimal-string prices, fill/order/position links, nullable closed-trade links, uncertainty window, `known_at_ms`, precision (`bar` or `modeled_open`), source references, coverage, synthetic flag and `SIMULATED`. Legacy G six-key markers are rejected. Partial coverage is retained. A marker beyond the information cutoff, duplicate fill, unknown field, malformed price, mismatched scope or changed artifact bytes fails closed.

This schema is a bounded chart-artifact acceptance seam, **not an agreed F/G production projection**. It does not transform F artifacts, resolve authentic source records, certify market completeness or prove all F fills were accounted for. F owns financial semantics; a future F/G/E projection must resolve and digest-bind actual immutable input/output bytes, verify complete fill/order/position correspondence and preserve F `time_window` semantics. No invented closed trade ID may be introduced. Current tests use an explicit synthetic `closed_trade_id: null` open entry.

## Verification and remaining gates

TDD: first test execution failed collection because `src.api.research_control` did not exist. After implementation the focused suite passes 27 tests. It includes a synthetic authenticated API submit > retained test-store job > reconstructed controller > artifact/status > digest ACK path. The same test store survives controller recreation; **this is not a process restart, durable PostgreSQL or crash-consistency test**. Negative fixtures cover auth expiration/revocation callback, object ACL, revocation during resolution, host/origin, arbitrary commands/fields, replay conflict, scope and digest tampering, future knowledge, uncertainty/ID loss, duplicate and excessive fills, and missing provider. Ruff checks/format are run on the two added Python files.

Run from this checkout with the shared development interpreter (read-only):

```
python -m pytest tests/platform/test_research_control.py -q
python -m ruff check src/api/research_control.py tests/platform/test_research_control.py
python -m ruff format --check src/api/research_control.py tests/platform/test_research_control.py
```

Remaining before integration: reviewed canonical PostgreSQL ledger and worker fencing; real expiring capability/authentication and owned provider bindings; source provenance resolution and complete immutable F artifact correspondence; F/G/E agreement on the v2 projection; D renderer and real render acknowledgment; process kill/restart/replay tests in a disposable canonical database. No physical ADMINIT acceptance, browser rendering, real-market research, live API/DB/job load, installed `C:\TesterLab`, UAC, service/ACL/registry changes, office PCs, real trades or main merge were performed. Draft PR evidence only.

