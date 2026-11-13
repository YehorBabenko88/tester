# Issue #60 / MASTER 2026-11-13: OPS evidence and blocking gate

Source pin: 3b2eef8a4139195db1a775545e83f1c1f58bf403.
Target: Draft PR #73 only; no main merge, no PC4/ADMINIT/office/PG5432 changes.

## Local isolated verification (Linux, Python 3.13.5)

Executable subprocess tests against exact committed blobs:
- scripts/ops_disposable_lifecycle.py: e1cb7fe6cd2d98f4fcf26016f562ce46881c114b
- tests/platform/test_ops_disposable_lifecycle.py: b128d12499069d6f44a322027890a942cc7e3e50

- PASS 14/14: two real Python child generations in one disposable root, cooperative
  stop via create-only request, real process-handle wait exit 0 and checkpoint;
  no restart while live; reject stale/foreign PID, OS birth, executable path,
  owner name and marker; reject reused stop marker, altered previous manifest,
  altered checkpoint, changed exact argv and a timed-out drain without kill.
  Five additional PASS cover tampering with an older generation, unexpected
  history files, symlinked evidence, unknown manifest schema, and modified
  fixed environment/DB configuration before restart.
- PASS 6/6 earlier standalone checkpoint unit tests in local ops_work copy.
  Those six are NOT an exact-SHA run of the current full GitHub branch.
- SKIP 3/3 previous real PostgreSQL tests plus SKIP 1/1 new integrated
  isolated-PG process/recovery test: PostgreSQL unavailable in local sandbox:
  no postgres/initdb/pg_ctl, Docker/Podman or psycopg module available.
  The new GitHub Actions portable-pg job is provisioned to test a disposable
  postgres:16 service on host port 55479, never a user/office PG5432.
  New tests/platform/test_ops_pg_lifecycle_real.py launches two real owner-bound
  Python workers in one disposable directory, verifies checkpoint persistence,
  queued recovery, attempt count 2, distinct lease tokens, old-token refusal,
  final completed state, and unchanged PostgreSQL system identifier and
  postmaster birth time. This integrated DB test is authored, NOT_RUN here.
- GitHub Actions result for new HEAD: NOT_RUN/UNKNOWN until run concludes.
- Windows native process/role/worker acceptance: NOT_RUN.
- Actual PC4 portable same-root restart: NOT_RUN; not implemented by this helper.
- Real ten-symbol sustained load: NOT_RUN. No hard RAM/CPU limits imposed.

## Resource observation (one real fixture child, not sustained load)

Read-only psutil measurement on one owned Linux Python process:
RSS 2,813,952 bytes; CPU 89.9% sampled over 0.1 second; 1 thread;
read_bytes=0, write_bytes=0; cooperative exit=0 and checkpoint present.
This is a single observation, not an upper bound, capacity result or an
operational performance claim. Unknown/NOT_RUN: actual PG, Windows process
limits, PC4 compatibility, ten-symbol duration, long-run peak and hard caps.
Separate JSON resource receipt is in docs/ops_master_resource_receipt.json.

## Precise production blocker

src/integration/pc4_runtime.py:serve intentionally refuses retained
.pc4-api.json, .market-api.json, .market-worker.json,
runtime/stop.request and pc4-shutdown.request records. src.api.worker
uses a fixed stop.request, not a generation-scoped stop mechanism.
The current installed candidate has no externally verified, birth-bound,
API+worker drain/exit witness and cannot be retroactively enrolled without
changing the immutable source. Removing marker files or swapping PIDs would
bypass ownership evidence and is forbidden.

The new disposable controller is explicitly NOT wired to the PC4 runner,
is single-controller/in-memory-handle only, and does not claim service
compatibility, startup after controller crash or Windows native attestation.
Its owner marker is test-injected; a production marker must be unpredictable
and owner-protected. It is not a release-ready restart implementation.

## Acceptance / rollback

Status: NO_GO for live portable restart and full OPS release. Do not deploy,
merge, turn on forced termination, elevate permissions or touch existing PG.
A new independently reviewed immutable candidate needs generation-scoped
worker stop/drain, persistent externally witnessed process identity,
authenticated API-worker ownership, explicit job checkpoint/lease bridge,
read-only preflight and real disposable PG integration + Windows acceptance.
Rollback for this PR is to discard the opt-in branch; no runtime action needed.
