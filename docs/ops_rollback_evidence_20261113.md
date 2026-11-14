# OPS: startup rollback and activation gate — 2026-11-13

Repository: YehorBabenko88/tester. Draft PR #73 only. No changes to installed
PC4 tester, main, PostgreSQL 5432, UAC, reboot, or force-kill.

## Confirmed baseline (GitHub Actions)

The *previous exact head* `6ae8323c520e26796cf781092fd8056da2ff869e`
passed its isolated OPS workflow, run 43470806719:
- `portable-pure` SUCCESS: 64 pure/process tests (from log suite totals).
- `portable-pg` SUCCESS: 5 actual PostgreSQL-16 tests on isolated host
  endpoint 127.0.0.1:55479, including lease fencing, checkpoint/recovery,
  same-root two-process generations and queue/WAL sampling.
- Verified run artifact `ops-dual-pg-evidence` ID 14061253174.

These are tests of a disposable fixture, **not** actual Windows PC4 native
restart. Other repository workflows had failures; OPS success does not
constitute overall release approval.

## New changes and local evidence

- The paired controller now creates a generation-scoped
  `activated.request` only after both processes are enrolled, the pair
  manifest is written, and current ownership is re-attested. The disposable
  PostgreSQL worker must validate this activation receipt before its
  **first DB job claim**.
- If API startup fails, the controller requests cooperative shutdown only
  of its exact enrolled child handles, records create-only `rollback.json`,
  and permanently refuses to reuse that partial generation. Successful
  cleanup is `SEALED_PARTIAL`; missing birth proof, nonzero exit, timeout
  or unverified ownership is `BLOCKED`. Neither status permits restart.
- An API failing after process creation triggers cooperative cleanup of
  **both** API and worker. No call to `kill()`, `terminate()`, SCM, UAC or
  reboot exists in this rollback path.
- New regression cases reject modified activation receipts, test both
  partial-launch rollback paths and the fail-closed case where an owned
  child cannot be safely stopped.

Tested locally against source with exact GitHub blob SHA:
- Existing single-process lifecycle: **PASS 14/14**
- API+worker process lifecycle including 5 new tests: **PASS 16/16**
- Read-only resource/queue/WAL mock tests: **PASS 4/4**
- New disposable-PG rollback scenario (failed API second generation): **SKIP**,
  no local PostgreSQL server/psycopg available. Added to the isolated
  `portable-pg` CI job. It must prove state `queued`, attempts `1`,
  checkpoint generation `0`, zero second-generation job claims and no
  restart over the rollback record.
- Hosted CI on the **new exact head**: NOT_RUN until GitHub reports a
  completed result. Never transfer baseline PASS to a new SHA.

## Resource and load gate

Earlier disposable PG test created real CPU/RSS and WAL/queue receipts, but
the new rollback test has no independent PostgreSQL result yet. A sustained
ten-symbol workload, hard CPU/RAM caps, WAL growth under sustained load, and
native Windows process exit attestation remain NOT_RUN.

## Production blocker and rollback

The installed PC4 candidate still lacks a generation-scoped worker stop
mechanism and birth-bound externally sealed API+worker exit witness; its
`pc4_runtime.serve` refuses retained stop/process markers. This feature
remains **NO_GO for PC4 deployment**, despite successful disposable tests.

Rollback of this PR: discard its opt-in branch without touching physical
installations. A failed disposable restart retains its root and receipts;
no markers are deleted, and no unverified processes are adopted.
