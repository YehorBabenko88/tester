# OPS continuation — PostgreSQL rollback and bounded queue-load acceptance

Scope: **only** `YehorBabenko88/tester` / Draft PR #73. No main merge,
no installed PC4 edits, no PostgreSQL 5432 connections, no Codex,
no UAC/reboot and no kill/terminate actions on API or worker.

## Authoritative completed baseline

Exact commit `6ae8323c520e26796cf781092fd8056da2ff869e`,
GitHub Actions run [43470806719](https://github.com/YehorBabenko88/tester/actions/runs/43470806719):
- **PASS portable-pure**: 64 tests.
- **PASS portable-pg**: 5 tests against disposable PostgreSQL 16 exposed on
  127.0.0.1:55479 (lease fencing, checkpoint, original in-flight job
  recovery, paired API/worker cooperative stop, queue and WAL).
- Independent saved artifact `ops-dual-pg-evidence`, ID 14061253174,
  reports queue depth 1 -> 1 -> 0, attempts=2, old lease fenced=true,
  WAL from 9,941,135 to 9,943,409 bytes (delta **2,274 bytes**),
  maximum worker RSS from two samples **55,152,640 bytes**.
  Instantaneous CPU 0.0% is an insufficient sampling method, **not**
  proof of low utilization. This short fixture is not sustained market load.

## New startup-rollback contract

API+worker are launched into a create-only generation with separate birth
proofs, marker, executable, argv, cwd, owner, process handles and stop paths.

A *generation-scoped activation receipt* is created only after both have
been enrolled and identity rechecked. The PostgreSQL worker must validate
the pair manifest hash and activation receipt **before claiming** any job.

An API startup failure triggers a cooperative stop of exactly those child
handles whose birth ownership is proven. Rollback is recorded as
`SEALED_PARTIAL` (verified stopped) or `BLOCKED` (unverified/live).
The failed generation is retained and **cannot be restarted or skipped**.
No force kill, no deletion/replacement of old receipts.

Local exact-Git-blob subprocess evidence: **PASS 34/34** (14 original
single-process, 16 paired rollback/activation, 4 metrics-mock).
Two new disposable PostgreSQL tests are locally skipped due absent PG.

## CI regression identified and corrected

New activation initially caused hosted `portable-pg` failure at commit
`39b760fe25a93065ec8e65cc070edbc5c4965301`: the *legacy test*
`test_ops_pg_lifecycle_real.py` still created a standalone worker, which
correctly refused to claim without a paired API activation.

It has been converted to use the paired API+worker controller instead.
The security gate remains enforced. **Do not claim this fix passed hosted
CI until the new exact SHA has a completed successful run.**

## Bounded ten-symbol real PostgreSQL queue load

New `test_ops_pg_load_real.py` uses **50 real durable jobs** across
10 synthetic symbol labels with four concurrent lease-fenced queue
consumers. It asserts unique claims, attempts=1, completed=50,
queue depth 50 -> 0 and samples read-only WAL, process RSS, CPU and I/O.
This is a bounded PostgreSQL transaction contention test, **not** Bybit
network load, not 10-symbol sustained market activity and not a hard
CPU/RAM enforcement acceptance. It is locally **SKIP/NOT_RUN** and must
be verified on the standalone PostgreSQL 16 GitHub service.

CI will upload `ops-load-pg-evidence.json` only when the actual test
finishes. A missing artifact or queued job is not a PASS.

## Remaining release gates

- Native Windows PC4 birth-bound externally sealed API+worker process
  exit and generation-scoped worker stop: **NOT_RUN**.
- Real installer/restart of the pre-existing PC4 source: **NOT_RUN/NO_GO**.
  Its retained process/stop markers intentionally prevent same-root reuse.
- Hard CPU/RAM limits, crash-safe controller recovery and real sustained
  ten-instrument market workload: **NOT_RUN**.
- The branch remains Draft and must not be merged into main.

Rollback of the proposed code: discard the opt-in PR branch. Existing
machines and PostgreSQL 5432 are untouched.
