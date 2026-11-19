# Data Guardian P0 — read-only reconciliation

Issue #85 · Draft PR #91 · owner: Data Guardian only.

## Status and boundary

Pure Python proposal engine in `src/api/guardians/data_guardian.py`. It neither connects to PostgreSQL nor calls Bybit, CatalogHandler, SQL, shell, Codex, LLM, or the Windows service manager. Default `enabled=False`; deployment and execution are NOT implemented. It cannot attest the actual database, original raw bytes, or history completeness on its own.

A trusted, independently implemented read-only adapter must supply a bounded snapshot with explicit PostgreSQL schema and DSN isolation attestations (port 55432, database tester_preview, role guardian_readonly), bounded_verified history state and immutable baseline SHA256, original projection checks, source record SHA256 comparisons, source evidence digest, gap-proof digest and exact scope/window, lease-scope digest, provenance and closed-candle checks. These boolean attestations are **inputs, not cryptographic verification**; a future independent verifier must produce them and bind them to immutable evidence receipts. Untrusted callers must not be allowed to set them.

## Decisions

- `ALERT`: incomplete/unknown baseline, missing schema, unverified source, invalid scope, 5432, open candles, unsafe window, missing source gap proof or fencing.
- `PROPOSE_QUARANTINE`: duplicate timestamps, source mismatch, hash/projection conflict, invalid OHLCV including NaN, positive/negative Infinity and negative volume. No quarantine storage mutation.
- `PROPOSE_BOUNDED_BACKFILL`: verified missing timestamps, only after independently attested source gap proof bound to the exact identity/window, CatalogHandler identity, queue ownership, epoch lease and no overlapping writer. Proposal includes exact missing timestamps, bounded range, idempotency evidence digest, and `execution_authorized=False`. This is not an enqueue operation.
- `OBSERVED_CONSISTENT`: only the supplied bounded sample; never claim complete source history.

The proposal's bounding window may include existing candles between missing timestamps. A future executor MUST revalidate every missing identity and exclude already present keys before queueing, using a fenced transactional adapter; it must independently reread both raw and original projection afterward. No executor exists here. Boolean attestations, SHA strings, database names and ports are caller-supplied and can be forged; they are not proof of real PostgreSQL isolation or source authenticity. A trusted adapter must validate the actual connection and cryptographically bind independent receipts. Noncontiguous missing keys remain an explicit list; no range overwrite is authorized.

## Windows ADMINIT smoke (offline, no database access)

From a disposable checkout of the exact PR HEAD:

```powershell
$env:GUARDIANS_ENABLED = 'false'
py -m pytest tests/platform/test_data_guardian.py -q
py -m ruff check src/api/guardians/data_guardian.py tests/platform/test_data_guardian.py
py -m ruff format --check src/api/guardians/data_guardian.py tests/platform/test_data_guardian.py
git rev-parse HEAD
```

Do not configure PostgreSQL credentials or start any service for these tests. Check the GitHub Actions exact-HEAD URL separately. Never run against PostgreSQL 5432 or active Preview.

## Unimplemented acceptance gates

Independent PostgreSQL reader and exact original-byte SHA attestation; trusted catalog source proof; durable append-only incident ledger; independent lease/fencing executor; quarantined-range persistence; negative disposable DB tests; physical ADMINIT validation; exact-HEAD CI and 24-hour soak. Until these pass, **NO-GO for activation**, `GUARDIANS_ENABLED=false`.

MUE-X influence: typed role boundaries and incident fingerprints only. No dynamic code execution, shell sandbox, blacklist permissions or automatic mutation of active code.

## Independent negative matrix (P0)

Pytest covers nonfinite OHLC and volume, impossible candle bounds, duplicate identities, conflicting source/projection SHA, provenance mismatch, unknown history, missing source/gap proof, incorrect scope/window, mismatched DSN role/database/port, stale lease epoch, overlapping writers, and discontiguous missing keys. These are proposal-level checks only; no database integration is claimed.

Shared foundation Ruff I001 belongs to MASTER #88; this workstream must not edit foundation files. Exact-head GitHub Actions and physical ADMINIT acceptance remain required.

## Reinstallation preflight (read-only; NO-GO until MASTER approval)

This component has **no installer, schema migration, database writer or rollback executor**. Do not run a Data Guardian on the existing installation. `GUARDIANS_ENABLED=false` must remain in the proposed environment.

Before any future installation, an independently authorized operator must inventory the existing Windows services (names, binary paths, identities, startup modes), listening ports, dedicated PostgreSQL instance/database/role, configuration and retained raw-data volumes, without stopping services or modifying the installation. The inventory must explicitly identify PostgreSQL 5432 as excluded. Compare a signed/pinned candidate HEAD and package digest against the planned release manifest. Do not copy credentials into reports.

For a disposable **isolated** Preview database only, require a consistent backup/snapshot and separate immutable checksums for raw OHLCV, provenance/source SHA, original projection, job checkpoints and relevant schema definitions. Independently verify backup readability and a restore into a distinct disposable database before any write-capable acceptance. No assertion of history completeness follows from a successful checksum or a bounded sample. Unknown baseline, stale source, invalid source proof, missing row identity, duplicate or corrupt candle means ALERT/quarantine proposal only.

Rollback contract (future, MASTER-owned): preserve the old installer, binaries, service definitions, configuration and verified database backup; record hashes and restore-test receipts; stop/replace only after an explicit operator gate and a proven safe shutdown; on failure, return to the prior pinned build and restore the **isolated** database only after an independent consistency check. Never overwrite raw source records or restore into PostgreSQL 5432. This document performs no backup, restore, stop, uninstall or deployment.

Installation readiness: **NO_GO**. Required: exact-head Data pytest/Ruff and fault-injection PASS, shared Foundation I001 resolution, independent source/DSN evidence reader, signed receipts, Windows isolated restore drill, MASTER compatibility approval and physical ADMINIT gate.

## ADMINIT full-uninstall acceptance — contract 13d26b74

This section supersedes the **retention/backup rollback** discussion above **for a user-confirmed full uninstall only**. The earlier backup/restore plan applies to a non-destructive reinstall, not to the destructive `DELETE TESTER` operation. Do not create or retain an archive/backup during full uninstall.

Data Guardian owns only independent acceptance criteria, **not** installer or uninstaller code. A disposable Windows fixture must establish an independently attested manifest binding of TESTER-owned dedicated PostgreSQL cluster root, instance identity, non-5432 port, dedicated service image path, raw OHLCV/provenance/source SHA storage, downloaded candle history, job checkpoints, and test-run results. Reject missing/unknown/foreign ownership; no inference from port number alone.

Before the destructive gate, independently verify that both explicit confirmations (including exact `DELETE TESTER`) were recorded, the exclusive installer lock is held, and all owned services have exited. Verify the dedicated cluster is not `postgresql-x64-18`, not the system PostgreSQL 18 data root, and not port 5432. No broad SQL DROP against arbitrary DSNs, and no recursive traversal of symlinks, junctions, or reparse points.

After a **fixture-only** uninstall, verify absence of every manifest-owned raw candle/history, original projection, provenance/hash evidence, job checkpoint, test-result directory and dedicated PG cluster; verify both shortcuts and owned services removed. Confirm protected system PostgreSQL identity and data hash remain unchanged. Missing ownership proof or partial deletion must yield FAIL, never a false `fully deleted` receipt. No backups/archives retained from the destructive fixture. Ordinary deletion does not guarantee forensic sanitization.

Data Guardian does not perform deletion, SQL, backups or installation. Production execution remains NO_GO pending MASTER #88 independent Windows acceptance.

## Consolidation audit / MASTER integration boundary (2026-11-19)

**Data-only ownership:** `reconcile` checks read-only OHLCV/provenance/gaps, `to_policy_event` converts a fresh proposal into the existing foundation event schema, and `assess_data_removal_receipt` checks **only** independent fixture evidence of database/history/test-data absence. The previous `assess_owned_cluster_removal` duplicated MASTER's `windows_install_gate.validate_manifest` and was replaced; installer root, service ImagePath, shortcuts, locking, exact `DELETE TESTER` confirmation and reparse checks are **not** implemented or repeated here. Storage handles filesystem safety.

**Integrity replay hardening:** quarantine evidence includes a bounded fingerprint of the actual bad row and immutable source/baseline evidence; gap idempotency evidence includes the exact missing timestamp list, source proof, baseline, lease epoch and lease proof. Any changed source or lease invalidates a previously generated `Finding` when `to_policy_event` independently recomputes the proposal. All quarantine/backfill remain `execution_authorized=False`; no SQL, exchange calls or queue effects.

**Unresolved cross-project contract mismatches for MASTER #88 / PR #94:**
- This P0 code and the foundation `guardian_policy.py`/`guardian_supervisor.py` require `environment=tester_preview`. The canonical Windows target is now `C:\TesterLab`, not Preview v3.5. A signed runtime identity/environment contract must be agreed before release, rather than weakening these fail-closed guards independently.
- Data P0 currently pins `database_name=tester_preview` and port `55432`. An installer-owned, independently observed dedicated cluster/database identity is still missing. Never infer isolation from port/name strings; do not touch system PostgreSQL 5432.
- The issue's requested `CatalogHandler` is not found as a class/module in the examined PR code/tree. Existing modules include `src/api/jobs.py:JobQueue` and `src/api/market_catalog.py:CatalogService`, but neither has been certified as an equivalent fenced candle backfill executor. Do not automatically substitute either; resolve the exact adapter/lease and after-check contract with MASTER before enabling.
- The standard Python CI `unittest discover` does not collect this pytest-function matrix. The existing acceptance chain fails earlier on a shared Foundation Ruff I001; MASTER owns its fix. A dedicated exact-head pytest job or independent execution receipt is required before merge.
- A `sha256`-shaped string and caller-provided boolean are **not** cryptographic proof. A separate authenticated read-only reader and verifier must bind receipts to the observed PostgreSQL instance, raw source bytes and source history. Live integration and physical ADMINIT acceptance remain blocked.

**Independent isolated test scope:** the Data module and the two foundation policy modules were copied into a disposable Python-only workspace and each Git blob SHA was checked against GitHub; an independently constructed pytest matrix checks unit, policy integration and destructive fault paths without DB access. This does not substitute for full exact-HEAD repository CI or PostgreSQL fault-injection. Release gate remains **NO_GO** and `GUARDIANS_ENABLED=false`.

## Transaction interruption and restart fault model (release gate)

The added Data-owned fault matrix deliberately injects a truncated reader batch, a rejected read-only attestation, an expired lease, a corrupted baseline, a foreign PostgreSQL identity, and a simulated restart/replay of immutable snapshots. The expected outcome is always a deterministic proposal or ALERT with no mutation of previously confirmed source rows. A restart replay must not become an executable authorization.

**These tests do not kill a PostgreSQL backend, interrupt a real transaction, or restart Windows services.** Those require an independently approved, disposable, non-5432 PostgreSQL cluster and a trusted read-only reader with before/after checksums. Required physical scenarios: transaction aborted before commit, crash after commit before acknowledgement, duplicate replay after reboot, incomplete source download, concurrent writer fencing, corrupted WAL/checkpoint, and full deletion of the dedicated TESTER database/history/test results with zero retained backups. Preserve original source bytes on all non-uninstall failure paths; after an explicit destructive uninstall, verify complete absence without touching system PG18/5432.

The Data Guardian module is not a database transaction manager or installer. The existing `JobQueue`, `CatalogStore` and MASTER install gate own separate concerns and must not be duplicated. Until the disposable PostgreSQL/Windows tests, signed DSN/source receipts and MASTER integration gates pass, **NO_GO** for release and `GUARDIANS_ENABLED=false`.

## Disposable transactional fault harness (not production PostgreSQL)

Three additional Data-owned pytest cases use a temporary **SQLite** database under pytest `tmp_path` to exercise partial insert rollback, confirmed-row preservation across connection restart and duplicate-key rejection, and loss of an uncommitted connection. These tests do not connect to TESTER, do not inspect ADMINIT and cannot establish PostgreSQL transaction/WAL behavior. Their role is to test the invariants and fixture methodology independently before MASTER authorizes PostgreSQL-specific integration tests.

**PostgreSQL release gate remains open:** use a separately provisioned disposable dedicated cluster with an independently attested non-5432 port and database, never the system PostgreSQL instance. Capture source/projection SHA before and after killed transactions, committed-but-unacknowledged replay, writer fencing, service restart, and a separate TESTER-only full deletion fixture. Explicitly verify system PG18/5432 and unrelated DB identities unchanged. MASTER controls installation and destructive operations. No release or automatic remediation before these physical results and exact-head CI are green.
