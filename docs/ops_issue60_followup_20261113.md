# OPS — MASTER Issue #60 follow-up, 2026-11-13

Repo: YehorBabenko88/tester. Scope: **Draft PR #73 only**.
Pinned source: `3b2eef8a4139195db1a775545e83f1c1f58bf403`.
Safety: no PC4, ADMINIT, office hosts, PostgreSQL 5432, main, Codex, UAC,
reboot, force-kill or forced worker termination.

## Executable local evidence

Separate Linux disposable temporary root, Python 3.13 and psutil 7.2.2:

| Suite | Result | What it proves |
|---|---|---|
| Existing birth-bound single-process generations | **PASS 14/14** | Cooperative exit and checkpoint, old PID/executable/owner, history integrity, refusal on live/foreign/partial evidence |
| New disposable API + worker pair | **PASS 11/11** | Both independently owned and externally awaited, exit=0, pair closure, two same-root generations, live-old refusal, foreign controller cannot adopt handles, tampered API/worker or pair-root witness refusal before stop/new spawn |
| Read-only PostgreSQL metrics with controlled mock rows | **PASS 4/4** | CPU/RAM worker sampling remains independent; SQL is read-only, queue-depth counts, WAL counter and PG incarnation comparison; unknown counters remain null |
| New real dual-process PostgreSQL integration | **SKIP 1/1 locally** | Exact disposable PG16 endpoint is required and not installed here |
| Existing isolated PostgreSQL suite | **NOT_RUN locally** | Requires psycopg and disposable PostgreSQL service |
| GitHub Actions latest exact HEAD | **QUEUED/PENDING until a finished run is available** | Do not infer PASS from queued |

The previous suites were rerun and the new suites executed in the sandbox before publication.\nOverall local: **PASS 29/29** (14 + 11 + 4), with real PostgreSQL **NOT_RUN**.
No GitHub Actions/Windows/PC4 PASS is claimed.

## Separate API + worker lifecycle

`scripts/ops_dual_lifecycle.py` creates generation-scoped API and worker
subprocesses under one disposable root. A fresh generation is permitted only
after both old processes have validated birth-bound Popen handles, exact
exe/cwd/argv/user/marker, successful cooperative exit, checkpoints and sealed
hash-linked evidence. Both previous child manifests/config hashes are verified
*before* launching either new process. Any missing, unexpected, foreign,
live, partial or corrupt evidence makes a restart REFUSED. The original
controller must remain alive: a new controller cannot adopt process IDs.

This is a **disposable harness**, not an adapter to installed PC4 services.

## PostgreSQL and resource evidence

`scripts/ops_resource_metrics.py` collects read-only PostgreSQL queue
counts (queued/running/paused/completed/failed), aggregate queued/running/paused
depth, cumulative pg_stat_wal.wal_bytes, PG system identifier and postmaster
birth. `wal_growth()` reports delta only if the original PG incarnation is
unchanged and the counter did not reset. All failures are explicit NULL/UNKNOWN,
never zero. CPU/RSS/thread and IO measurements are birth-bound process reads.

`tests/platform/test_ops_pg_dual_real.py` runs two **real** API+worker fixture
generations against strictly allowlisted disposable PostgreSQL 16 host port
**55479**, checks in-flight job checkpoint, queue transitions, lease token
rotation, stale-lease rejection, exactly two attempts, completion, both
process receipts and WAL/queue samples. Hosted CI uploads
`ops-dual-pg-evidence.json` only after successful observations.

Locally no PostgreSQL server/psycopg/isolated cluster is available.
Therefore real PG lease/recovery/WAL is **NOT_RUN**, NOT PASS.

### One physical two-process fixture sample (not sustained load)

- API: RSS **2,895,872 bytes**, CPU **99.8%** sampled for 0.1 seconds, threads 1
- Worker: RSS **18,403,328 bytes**, CPU **99.8%** sampled for 0.1 seconds, threads 1
- Both independent cooperative exit codes: **0**
- API/worker disk IO observed **0/0 bytes**
- Queue, WAL, PG incarnation: **NULL / NOT_RUN**
- Ten-symbol sustained run, hard CPU/RAM caps, PC4 native acceptance: **NOT_RUN**

The values are one process snapshot, not a capacity forecast, cap guarantee
or sustained performance result.

## Exact production blocker / rollback

The existing `src/integration/pc4_runtime.py:serve` blocks restart when
retained API/worker/stop records exist. `src.api.worker` still uses the
fixed `runtime/stop.request`. The installed old candidate never enrolled a
generation-scoped API+worker externally sealed exit witness. Under immutable
source requirements it cannot be retroactively labeled safe by deleting
records or changing PIDs. Production same-root restart is **NO_GO**.

A distinct reviewed immutable candidate must implement native dual-process
birth-bound witness, generation-scoped worker stop/drain, durable PG lease
fencing and crash-safe controller recovery. Windows physical acceptance and
actual sustained ten-symbol metrics remain separate gates.

Rollback: discard the optional Draft PR branch. Nothing installed on PC4.
