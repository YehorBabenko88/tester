# MASTER supervisor P0 — Issue #88

Status: Draft, no deployment. Default GUARDIANS_ENABLED=false.

The owned module `src/api/guardians/supervisor.py` provides a dedicated
SQLite receipt journal with SHA-256 hash chaining, durable idempotency,
transactional lease admission, per-scope fencing epochs, restart admission
budget, bounded active leases, and persistent kill switch. It never executes
proposals, restarts a service, sends orders, deletes files or accesses PG5432.
It is a proposal-only component, not an autonomous remediation engine.

Security limitations: local hash chains are tamper-evident only against
nonprivileged edits; privileged rewrite can recompute hashes. External immutable
anchoring, restricted Windows service SID/ACL and independent verifier are
required before production. Lease expiry uses wall-clock time and assumes
a single SQLite authority. `finish(verified=True)` records an external
assertion, NOT independently verified recovery. No execution or release
authority is granted by a reservation.

## ADMINIT isolated acceptance — NOT YET RUN

1. Check out exact PR #89 HEAD in a disposable worktree, never active Preview.
2. Run `py -3.12 -m pytest -q tests/platform/test_guardian_supervisor.py`.
3. Run `py -3.12 -m ruff check src/api/guardians/supervisor.py tests/platform/test_guardian_supervisor.py tests/platform/test_guardian_policy.py`.
4. Verify disabled-by-default, persistent kill after process restart,
   duplicate events, tampered receipts, expired leases, epoch fencing,
   budget exhaustion and concurrent admission.
5. Record read-only before/after service identity and checksums for PostgreSQL
   5432; any mutation is NO_GO.
6. Inject process crash, hard reboot and disk exhaustion in isolated Preview.
   Verify receipt chain and refusal of stale leases after restart.
7. Run 24h soak with bounded memory/disk, fault injection, exact-HEAD logs,
   independent verification and zero unauthorized effects.
8. Attach CI URL, exact SHA, PASS/FAIL, evidence and blockers to Issue #88.
   No merge/deploy without manual acceptance.

MUE-X source reviewed: swarm roles, correction fingerprints, memory lattice,
security guard and subprocess sandbox. Borrow typed coordination and verified
memory concepts; reject runtime arbitrary Python/AST mutation, shell execution,
string blacklist as security boundary and untrusted subprocess sandbox.

Ruff I001 ownership: shared tests/platform/test_guardian_policy.py belongs
to MASTER/foundation, not Code Guardian #90. Resolve only after exact-head
Ruff diagnostics; no unsupported claim of a fix or CI PASS.
