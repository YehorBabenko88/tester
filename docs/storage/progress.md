# Execution ledger — docs/superpowers/plans/2026-11-07-storage.md

Ruling: use the dedicated clone on workstream/b-integrity, already isolated from all other agent checkouts; no extra worktree needed.
Ruling: user explicitly authorized implementation after approving design; continue inline without another plan approval round.
Pre-flight: models -> Store -> operations -> crash/backup tests share envelope 1.0 and schema tester_storage; integration signatures documented in contract.
Baseline: branch B contains no discoverable tests (to be verified with unittest). PostgreSQL 18 installed and port 5432 responds; test cluster will be separate from existing server.

Task 1: complete. Watched ten initial model/quality tests fail, then pass. Added regression tests for exact seconds/us/ns and numeric-formatting replay; current unit suite has 12 tests.
Task 2: complete. Watched nine missing migration/publication integration tests fail against PostgreSQL, then pass. Migrations 001–004, append-only raw/provenance, coverage/checkpoints and bounded keyset read API added. Upgrade from v1 preserves confirmed raw data.
Task 3: complete. File tamper, safe provisioning and aggregate/metadata operations tested. Runtime password authentication checked with SCRAM; test SecretStore is an in-memory adapter (OS vault backend itself not exercised in sandbox). Existing foreign objects refused; no existing server/database/role modified.
Task 4: local verification complete; Draft PR publication follows. Kill-before/after-commit, blocking concurrent writer and pg_dump/pg_restore tests all passed. Independent reviewer found trade-interval false coverage, sparse observations false COMPLETE and retry starvation. All three reproduced as failing regression tests and fixed. Funding requires exact source event inventory; OI/ratio require known contiguous cadence. Review's backup boundary disclosed: external file backup and production role remapping are separate.

Final local full suite (2026-11-07): `python -m unittest discover -v` with TESTER_TEST_ADMIN_DSN for isolated PostgreSQL 18.4, Python 3.14.0/Windows, 38 tests, 9.861 seconds, OK, zero skips. Durability settings fsync/synchronous_commit/full_page_writes enabled. A fresh synthetic cluster run through run-isolated.ps1 also passed the then-current 36 tests. First initdb experiments hit a 60-second sandbox startup timeout; published PowerShell runner has no such timeout, and completed successfully. No test against production data or live Bybit completeness is claimed.

Coordination: approved decision posted to Issue #2; initial messages delivered to A/C/E/MASTER. A acknowledged contract. Follow-up tool call was refused because approval was required but unavailable; shared Issue #2 carries progress and final contract/PR links.

Final follow-up: checkpoint late-retry regression reproduced with two failing tests and fixed under the publication lock. Branch suite now 40 tests/10.455s, OK; main+B snapshot 49 tests/10.625s, OK. Independent targeted re-review confirmed all three earlier fixes. Draft PR #13 created; hosted Actions queued at last inspection.
