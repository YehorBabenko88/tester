# H execution evidence — 2026-11-07

Baseline HEAD: `3837f1a2b95343f6ad6d24a88ba89a7a438e78f3` — original 10/10 PASS.
Python 3.14, Windows. Existing PostgreSQL 18.4 binaries used for a newly initialized
disposable cluster under the task work directory, loopback port 56387, shared_buffers
32 MiB. No service registration, existing database changes, exchange orders or merge.
Initial port 55439 was already occupied; startup failed and testing moved to 56387.

## Executed checks

- H suite: 42 methods, 41 executed PASS, one UI skip (local Streamlit not installed).
  Includes 10 real PostgreSQL tests, simultaneous same-request retry, conflicting
  request, ten instruments, tenant isolation, revocation, rollback and fresh client.
- Baseline repository discovery: 9/9 PASS (separate discovery needed for H folder).
- Real F engine smoke PASS at `998c5bc89d1b35c9ad3047b1e50dcb1295120e31` using
  F's synthetic strategy/snapshot; separately approved hypothetical result only.
- Bounded import 1000-row tracemalloc peak <32 MiB PASS; oversized raw input denied.
  This is not maximum-size memory/disk acceptance or million-row storage stress.
- Synthetic demo in separate Python processes, PostgreSQL immediate stop and WAL
  recovery between executions: identical canonical result bytes/artifact PASS.
  Original byte comparison exposed JSONB key reordering; CLI now sorts object keys.
- `compileall` for H and workspace PASS; `git diff --check` PASS.
- Local UI AppTest cannot run because `streamlit` is absent; no browser-render
  or D-render acknowledgement claimed. CI includes dependencies and actual AppTest.

## Independent review

Fresh read-only reviewer reproduced tests and found persistence admission bypass:
direct Store.put could persist extra fields/unconsented source after rehashing.
New regression first failed on a fresh tenant; shared dataset validation now checks
exact fields, normalized scope, hashes, row and byte limits before storage. PASS.
Reviewer found no further critical/important issue in revision selection, tenant
queries, locking and revocation. Later UI/statistics additions retain separate tests.

## Current limitations / remaining integration gates

No live public trader history admitted. Native exchange export mapping unsupported;
canonical authorized exports supported. Consent and completeness are attestations.
B has not approved the isolated H snapshot schema/retention; normalized production
tables and physical purge not implemented. E HTTP authentication/durable queue,
G Codex capability/approval transport and D official renderer remain peer gates.
H exposes callable contracts and a runnable local research workspace, not evidence
of cross-workstream synchronization. Result digests ensure consistency, not signing
or third-party audit. Fees/funding/net/equity risk and real trader strategy unknown.
Do not call this full system READY until those gates have exact-head acceptance.
