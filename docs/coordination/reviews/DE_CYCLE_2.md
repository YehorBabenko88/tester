# Supervisor review cycle 2 — Issues #15 / #17

Read GitHub issues #15 and #17 and refreshed branch collection. Heads unchanged: E Draft PR9 `c2fb7b66f9ebc0b4b04519ea327c3baafc1e5f54`; D Draft PR12 `1b35ec6565ef0f9471ecd7197a64bf598080f85b`. Immutable sources under work/review-de-latest/{9,12}; findings assess these published heads against the newer requirements. No service/process installation, database provisioning, remote writes, or production source edits.

## Verified behavior

- E existing operations tests: `../../venv/Scripts/python.exe -m pytest tests/platform/test_operations.py -q --basetemp=../pytest-cycle2-operations` from scratch E checkout: **3 passed, 1 skipped** (symlink privilege). Checks cleanup ownership, quota/config guards, and separate-component log rotation.
- Controlled `probe_cycle2.py` executed actual worker.process_one and JsonFormatter: handler exception containing a synthetic DSN secret persists only `handler_failed`; safe job log stores exception type, not message. Cooperative shutdown path releases lease instead of marking complete. Formatter drops a non-allowlisted DSN extra field. No live database involved.
- D Node model suite repeated: **6 passed**. Canonical metadata adapter adverse probes repeated and still reproduce unavailable promotion/reason loss; native D envelope unavailable reason remains visible. Exact scripts are saved in scratch.
- API test retry with root venv and workspace TEMP/TMP still hangs before any completion and was interrupted after bounded attempt. Prior thread/network permission did not resolve it. No HTTP shutdown/admission/queue-limit pass is claimed; inspect static conclusions below separately.

## E missing or unsafe versus Issue #15

1. **No SCM Windows Service or dry-run lifecycle.** src/api/supervisor.py:1 explicitly says Foreground supervisor. Start-Tester.ps1:11 uses hidden Start-Process; Install-Tester.ps1 has no New-Service/SCM wrapper, service identity, delayed auto start or service failure action setup. No repair/uninstall/status script exists in complete tree. `Get-Command Install-Tester.ps1 -Syntax` lists Python/Database/Role/Port/MinFreeBytes/NoStart only: no WhatIf/DryRun. Inspected all three script bodies and emitted service/dry-run capability checks; both false. Running installer as a “dry run” would make real venv/runtime/ACL/DB changes, so was deliberately not executed. Service dry-run gate is **NOT_IMPLEMENTED**, not passed.

2. **Shutdown lacks admission drain; queue unbounded.** main.py:175-180 always admits POST /api/downloads after disk check; no stop.request/draining state checked. jobs.py:29-36 inserts every request without capacity/admission/idempotency key. Frontend's 100 tracked-job cap is client-local and does not bound server queue. Worker process_one:18 claims before checking stop; controlled probe with stop already set still claims then releases. Add shared drain state and queue capacity check atomically with admission, and reject before claiming if stopped. Supervisor's 20s wait then terminate/kill is escalation, not evidence of a drained transaction/checkpoint.

3. **Resource/fairness controls incomplete.** Settings offers disk quota/free-space and lease duration, not CPU/RSS/network/provider concurrency budgets. One worker runs one process_one serially; claim chooses oldest created_at, with no per-symbol/provider fairness, request queue limit, rate-limit admission, or Retry-After-aware scheduler in E. Adapter provider owns transfer behavior, but no enforced platform boundary proves these Issue15 controls. No 10-instrument million-trade peak-RSS/IOPS/tail-latency evidence exists in these tests. Do not reinterpret single-worker serial execution as fair parallel downloads.

4. **Logging avoids known exception leaks but does not redact message text.** logging_setup.py:14 uses record.getMessage() verbatim. Probe logging a synthetic DSN in event message retained `fixture-secret`; allowlisted extra protection does not cover message text. Current internal worker calls use fixed event names and were safe in the exercised path, so this is a hardening/integration requirement before arbitrary provider diagnostics are allowed. Add central redaction or enforce fixed event identifiers and sanitized diagnostics. No trace/run ID support (only job_id/error_type/component/storage/free/used allowlist), and no EventLog implementation; EventLog is optional under Issue15.

Supervisor does implement exclusive lock, separate API/worker children, bounded exponential child restart delay and five-restarts-per-minute limit. DB lease fencing/checkpoint transactions exist. These are useful primitives, but SCM lifetime, reboot/hard-kill, real shutdown TX and recovery gates remain untested here.

## D missing or incompatible versus Issue #17

- **Canonical unavailable records still become drawable PARTIAL.** app.js:96 ignores row availability/coverage, coerces structured provenance to `[object Object]`, substitutes generic reason. Actual fixture availability={status:'UNAVAILABLE',reason:'source_conflict_fixture'} produces PARTIAL with rows retained for drawing at :123. Canonical metric unavailable reason also discarded (model.js:12-15). This remains a blocking data-truth defect; prior review details in work/review-de-latest.md.
- **W/M and history navigation incomplete.** index.html:13 offers 1/5/15/60/240/D only; intervalMs app.js:16 understands D/numeric only. Charts request hard limit1000 (:17), next_cursor yields PARTIAL/narrow-range instruction (:105), no tile/cursor traversal for charts. It is honest about truncation but does not meet Issue17's history navigation requirement.
- **Strategy workspace not implemented.** No brief parser/DSL preview/run controls, SL/TP/fees/slippage/risk configuration, journal/equity/drawdown/entries/exits/failed signals overlays, synchronized journal navigation, deterministic export in published frontend. These are new Issue17 scope gaps rather than a regression of earlier PR12 promises.
- Current canvas supports mouse wheel zoom anchored to cursor, drag pan/shift range selection and shared timestamp hover. No keyboard shortcut handler, resizable panels, vertical price-scale interaction, or horizontal crosshair found. High DPI and browser performance tests were not rerun; six Node checks do not prove those acceptance criteria.

## Minimal safe root integration changes recommended

1. E: compose provider output through approved shared validate_record/typed envelopes before response serialization; preserve availability/reason/coverage/precision/provenance and reject NaN/invalid decimals. Current old simplified models silently drop metadata; avoid root maintaining a competing analytics engine.
2. D: lossless canonical adapter as a small separately tested function; unavailable/unverified rows stay unavailable, partial reason and inherited provenance survive, coverage kept separate. Only layout transformations belong in browser. Do not mask incompatible E records with AVAILABLE defaults.
3. E: add explicit drain flag + bounded atomic queue admission and no-claim-after-stop tests; expose health/readiness separate from provider capability. Keep SCM installation disabled until generated dry-run plans, least-privilege identity, restart policy and disposable-service tests exist.
4. D/E: agree server tiles/cursors and W/M calendar semantics before enabling navigation beyond1000; strategy lab remains unavailable until F/C provide validated simulation outputs. Root integration should not fabricate those capabilities just to clear a UI gate.

## Parallel root coordination diagnostic

On root request, read-only list_artifacts returned []; git worktree list in work/verify-final showed only original verify-final checkout (integration/master HEAD9766c0093e2608280875ac56db074414a3d62c69). No duplicate worktree creation was attempted. This is a point-in-time observation, not the eventual app operation result.

