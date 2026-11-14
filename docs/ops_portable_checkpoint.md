# OPS portable cooperative lifecycle (Issue #60)

This module is **opt-in and not wired to the live portable runner**. It is a
safe integration building block, not evidence of a successful PC4 restart.

- Capture owner instance ID, PID, OS process creation token and canonical
  disposable root from a trusted local observer. Re-read all four immediately
  before signalling the instance's **existing cooperative stop mechanism**.
  Never infer ownership from PID alone. On mismatch, refuse to act.
- Ask workers to stop accepting new jobs, finish or durably checkpoint active
  jobs, flush PostgreSQL commits, then persist the checkpoint. Do not kill a
  worker on timeout; return NOT_READY and leave it running.
- Use a dedicated, nonproduction checkpoint path. The atomic replacement and
  SHA256 detect interrupted/tampered snapshots, not malicious writers.
  Secure its directory with OS ACLs when integrating.
- On restart, verify schema and instance, reconcile IDs with authoritative
  durable completed jobs, and requeue only outstanding IDs. Enforce unique
  job IDs and idempotent DB writes in the real queue. Never restore arbitrary
  commands from a checkpoint.
- Sample RSS, CPU and thread count through a read-only psutil process adapter.
  A hard RSS/CPU cap and ten-symbol sustained load are **NOT IMPLEMENTED**.
  Implement resource enforcement in the actual worker, not by terminating
  processes in this helper.

Run isolated unit tests: `python -m unittest tests.platform.test_portable_ops -v`.

## Acceptance status

- Pure checkpoint/ownership/sample unit tests: PASS in disposable Linux
  harness (6 tests, 2026-11-13); GitHub CI NOT_RUN at publication.
- Live runner integration: NOT_RUN.
- Actual stop/restart and crash recovery: NOT_RUN.
- PostgreSQL durable reconciliation and 10-symbol sustained load: NOT_RUN.
- PC4, ADMINIT, office, Windows SCM, PG5432: UNTOUCHED.
- Release gate: NO_GO. Rollback: do not import or invoke this opt-in module;
  remove this branch/PR from candidate build. No runtime rollback needed.
