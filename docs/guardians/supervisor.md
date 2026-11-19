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


## Security audit follow-up

Receipt admission now compares the persisted role, status AND action reason;
a caller cannot rewrite `PROPOSE_OWNED_RESTART` into a review proposal to
evade the restart budget. Lease completion requires the exact persisted
reservation sequence and fencing epoch. Reusing a proposal after a reservation
is rejected. New negative tests cover forged receipt objects and reopen of
the dedicated SQLite store.

**Still NO_GO:** an attacker with direct SQLite write permission can forge
records and recompute the unkeyed chain; tail truncation is not independently
detectable. Production needs append-only external anchoring or signed
checkpoints, verified on startup, with restricted service identity. This
prototype does not prove an independent after-check. Wall-clock lease expiry,
lease release after crash and clock rollback must be fault-injected. The
`observed_volatility.py` edit was out of MASTER ownership and restored
byte-for-byte to the parent branch blob.

Ruff I001 in shared policy tests is **not reproduced** in this session:
the current file has a single sorted import statement. Capture the exact
Ruff version and failing log before modifying unrelated lines. Historical
CI failure from PR #90 is not proof that this HEAD fails. CI is not reported
as PASS without an actual exact-HEAD run.


## 2026-11-18 independent fault review

Journal admission now validates the existing SHA chain and receipt sequence
continuity under `BEGIN IMMEDIATE`. SQLite AUTOINCREMENT high-water sequence
detects naive deletion of the last receipt as well as interior gaps. Any
mismatch aborts a write and raises `guardian_receipt_chain_corrupt`.
This is defense in depth, **not** cryptographic protection against a
privileged SQLite actor able to alter `sqlite_sequence` or rewrite all hashes.

Windows physical gate must test: two separate processes contending for the
same scope; crash between lease acquisition and completion; process reopen;
expired leases and wall-clock rollback; forged receipt identity/action;
journal body modification, middle deletion, tail deletion and full rewrite;
persistent kill; restart budget over reboot; disk-full and SQLite corruption.
No success should be inferred from an executor's own report. Require an
external independent verifier and signed external checkpoint before enabling
any effectful adapter.

Ruff 0.17.4 Platform logs on HEAD `eee0a33` still reported I001 in the
foundation test. Replaced the multi-name import with a single module import
on the newer branch. Exact-head CI remains the authoritative check.


## Windows ADMINIT reinstall preflight — PREPARATION ONLY (2026-11-19)

This is a NO_GO contract, not a deployment script. No existing installation
may be stopped, uninstalled, overwritten, migrated or cleaned during preflight.
No privileged or real trading credentials are allowed. Keep
\`GUARDIANS_ENABLED=false\` in the candidate and current environment.

### Immutable candidate manifest

Before any physical acceptance, record the complete 40-character Git HEAD,
Draft PR number, exact-head CI run URLs and conclusions, artifact SHA-256
digests, wheel/package lock digests, Python/Windows architecture and version,
dependency SBOM, release signature and trusted signer identity. Reject
unsigned, unpinned, mixed-head or incomplete bundles. Compare candidate
artifact hashes against a separately stored signed manifest.

### Read-only inventory and isolation

Use an elevated **read-only inspection session** to export the Windows SCM
service names, executable paths, service SIDs, startup types, running states,
recovery policies, task scheduler entries, port listeners and process owners.
Inventory the current TESTER installation directory, config keys (redact
secrets), dedicated PostgreSQL cluster identity, port, database, tablespace,
volume and schema migration level. Record current Preview HTTP health and
exact release identity. Verify that the separate PostgreSQL 5432 instance
and all unrelated services are outside every proposed change set.

Example read-only commands (do not execute from this document):

\`\`\`powershell
Get-CimInstance Win32_Service | Where-Object {
  $_.Name -match 'tester|postgres' -or $_.PathName -match 'tester'
} | Select-Object Name,State,StartMode,StartName,PathName
Get-NetTCPConnection -State Listen |
  Select-Object LocalAddress,LocalPort,OwningProcess
Get-ScheduledTask | Where-Object TaskName -match 'tester'
\`\`\`

Never export plaintext passwords, connection strings or private keys.
Record ACLs and free disk space for candidate, data, WAL and backups.

### Backup and rollback contract

A designated operator must first approve a verified, application-consistent
backup of the **dedicated** TESTER database, journal, user data, configs,
service definitions and the currently installed release. Check backup hashes,
file ACLs and restore viability in a separate disposable environment. Preserve
original backup media and a second immutable copy. Require an independently
verified restore drill with row identities, provenance hashes and job queue
checkpoints; no fabricated data or schema rollback against live storage.

The future installer must be side-by-side and non-destructive: separate
versioned binary directory, explicit data volume mapping, separate dedicated
database, no writes to existing Preview or port 5432, and atomic activation
only after a human-approved maintenance gate. On failed verification, abort
activation and retain old binaries, data and services unchanged. If a
migration is irreversible, stop and require a forward-recovery plan instead
of pretending a binary rollback restores data.

### Independent acceptance and stop conditions

1. Exact-head Linux/Windows Python, Ruff and negative fault-injection PASS.
2. Separate disposable Windows ADMINIT acceptance, including process kill,
   journal corruption/tail truncation, forged receipt, concurrent lease,
   restart budget across reboot, clock rollback, disk-full and disabled mode.
3. Explicit service/data/port identity comparison before and after isolated
   tests, including confirmation PostgreSQL 5432 is untouched.
4. Backup restore drill PASS and independently signed manifest PASS.
5. 24-hour isolated soak with bounded memory, disk, receipts and zero
   unauthorized effects. No effectors may be enabled during this stage.
6. Human sign-off for any future maintenance window and rollback ownership.

Any unknown identity, failed CI, incomplete backup, broken signature, false
verification, unrelated service change, or missing rollback evidence means
**NO_GO**. This preparation does not authorize installation.
