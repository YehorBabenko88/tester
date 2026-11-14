# OPS native launcher reservation: fail-closed proposal

Source-only, opt-in, within Draft PR #73. The installed PC4 runtime and
PostgreSQL 5432 are not changed.

## New component

`scripts/ops_native_admission.py` implements a create-only reservation
before a future native API/worker process launch. Each native generation
is an immutable four-digit directory with an exact pinned source/config/root
and PostgreSQL system identifier/postmaster incarnation.

`NativeAdmission.next_generation()` requires all preceding generations to
retain valid reservation + closure records, an externally supplied original
process-handle exit witness for **every generation**, and a current read-only
PostgreSQL incarnation/lease-state preflight. An old `running` lease is
refused (no automatic promotion from stale or unknown); a failed rollback,
partial/crashed launcher, unexpected file, symlink or duplicate generation
cannot be skipped or reused. At generation 32 the system refuses by bound.

`reserve()` atomically claims one new generation directory; failure retains
that partial directory. It never unlinks or rewrites evidence. A closure is
recorded only if two independent booleans representing verified original
handle exit and lease fence are true. These inputs are **injected proof
contracts**, not yet bound to the Windows process and real PG observers.

## Verification and limitations

Pure regressions cover duplicate launcher contention, lost-controller
reservation, refusal to adopt a forged closure, changed PG system identifier,
live job lease, tampered reservation, unexpected paths, rollback retention
and missing lease fence. Isolated OPS CI runs them without connecting to any
PostgreSQL or spawning Windows-native processes.

**Important:** The module is a safety preflight / reservation *primitive*,
not a complete executable native Windows launcher. There is no resilient
handle transfer across a controller crash, no Windows service/PC4 runner
integration and no native Windows PostgreSQL lifecycle E2E. After a
controller crash the policy is to refuse restart and require independent
authorized intervention. This is intentional, not automatic recovery.

Previously validated exact HEAD `8c4c6cd17a5917058d4931761ffb6266ce4719c5`
had OPS workflow `43488352607` SUCCESS on portable-pure,
portable-pg and windows-native-witness. New HEAD CI must be independently
checked. Do not claim prior-run status for the new commit.

Native PC4 restart: **BLOCKED / NO_GO**. No kill/terminate, UAC, reboot,
Codex, PC4, PostgreSQL 5432 or main changes.
