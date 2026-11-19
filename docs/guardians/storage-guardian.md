# Storage Guardian P0 — Windows ADMINIT

**Status:** proposal-only; not deployed; GUARDIANS_ENABLED=false. Owner: Issue #86 / Draft PR #92.

## Capability boundary

`src/api/guardians/storage_guardian.py` reads disk free space and enumerates only immediate children of an explicitly supplied `runtime/tmp` directory. It never deletes files or creates directories, never connects to PostgreSQL, never calls a shell, and never modifies services or download queues. Candidate names must end in `.part`, be regular files and have mtime at least 24 hours old. Symlink, junction and Windows reparse-point ancestors and entries fail closed. Inventory is bounded to 10,000 immediate entries.

A manifest contains file basename, size, mtime_ns, device and inode plus a SHA256 digest of the observation. This is an evidence fingerprint, **not a signed approval or a race-free deletion token**. Windows junction and reparse checks rely on lstat st_file_attributes. An executor must independently revalidate all components and exact identity immediately before action, and prevent TOCTOU using OS-level handle-based checks. Existing `src/api/maintenance.py:cleanup_temp` is the only allowed future deletion adapter, after MASTER review; it currently lacks handle-based race protection, so P0 deliberately has no executor. A cleanup proposal does not authorize deleting even one file.

Historical candles, WAL, PostgreSQL files, audit records, metadata, backup, releases, other tmp extensions and nested files are never cleanup candidates. The `tmp_direct_bytes` metric is **not** a database or whole-runtime quota measurement. Quota warning is limited to directly inventoried tmp files. Low disk emits a proposal to pause **new** downloads, never interrupts a running job.

## ADMINIT read-only smoke (disposable fixture)

In a separate clone/worktree of Draft PR #92, with no service restart or administrator elevation:

```powershell
$env:GUARDIANS_ENABLED = 'false'
python -m pytest tests/platform/test_storage_guardian.py -q
python -c "from src.api.guardians.storage_guardian import inventory; print(inventory('C:/nonexistent-guardian-fixture', min_free_bytes=1000000000, quota_bytes=1000000000))"
```

For enabled sensor-only verification, create a **new disposable test directory** outside Preview; call `inventory(test_root, min_free_bytes=..., quota_bytes=..., enabled=True)`. Never point a test at live `runtime` and never call `cleanup_temp` in physical acceptance.

## Gates and evidence

1. Exact PR HEAD CI: pytest on Windows and Linux; confirm no new imports requiring LLM/Codex.
2. Negative tests: disabled, wrong budget types, missing/escaped root, symlink tmp/file, young/non-part/nested files, no effector.
3. ADMINIT: independently verify junction and reparse-point rejection, controlled low-disk telemetry fixture, no mutations, process stop/restart and 24h soak.
4. MASTER must approve any adapter integration; no merge, enablement or deployment without physical acceptance.

MUE-X ideas used: role-specific typed evidence and verified incident fingerprints. Explicitly excluded: unrestricted commands, subprocess sandbox as security boundary, self-modifying code, autonomous patches and model-controlled deletion.

## P0 follow-up security review (2026-11-18)

- `_safe` now uses two explicit checks, `stat.S_ISLNK` and `st_file_attributes & 0x400`; no ambiguous ternary precedence.
- The runtime root must be absolute and contain no lexical `.` or `..` components. Every ancestor is checked with `lstat`, including the direct tmp and candidate file.
- Enumeration is bounded using `os.scandir` (no unbounded `list(iterdir())`). More than 10,000 direct entries fails closed with no manifest.
- Reject invalid/NaN/infinite clock values, young files, non-.part names, directories, symlinks and reparse entries. Re-check tmp after scanning; any detected root change invalidates the manifest.
- **TOCTOU remains a release blocker**: a race after the final check, or candidate swap between `lstat` and use, cannot be made safe with pathname-based Python checks alone. There is no cleanup executor and no permission to use `maintenance.cleanup_temp` in this P0.
- Windows junction creation and real NTFS reparse behavior require physical ADMINIT acceptance. Simulated `st_file_attributes` is a unit test, not physical evidence.
- No automated deletion or pausing is performed. No WAL, PostgreSQL, backup or historical file can be selected for cleanup.

## Reinstallation preparation gate — 2026-11-19 (NO-GO)

This section is a **read-only preflight checklist**, not an installer and not authorization to stop services, copy live databases, delete temp files or overwrite existing data. MASTER #88 owns installer compatibility, migration, backup and rollback approvals. The Storage Guardian remains disabled and must never run during installation.

### Inventory evidence required before any installation approval

- Record existing Windows service names, executable paths, startup types, service accounts, current states and dependencies, **without stopping them**.
- Record candidate release SHA, manifest SHA256, artifact signatures, runtime version, installer target and data-root paths; compare with existing installation and check for overlaps.
- Identify dedicated PostgreSQL Preview instance and its port using read-only configuration/service inventory; verify the production PostgreSQL 5432 instance is excluded. No SQL writes or PostgreSQL maintenance.
- Record free disk bytes, expected package/staging/backup sizes, and reserved rollback headroom. Do not infer database size from `tmp_direct_bytes`; independent volume-level inventory is required.
- Verify backup provenance and successful independent restore into a disposable **isolated** environment before any installation GO. A backup file merely existing is insufficient.
- Pin old executable/configuration version, database/schema compatibility and a rollback decision point. Require a human-approved rollback plan that preserves historical candles, WAL, backups and all original files.
- Validate that no reparse/junction/symlink crosses candidate staging, runtime, data, backup or rollback roots. Use OS handle-based verification in any later effectful installer; pathname-only `lstat` is insufficient against TOCTOU.
- Require Windows ADMINIT physical smoke, exact-head Python/Ruff/fault-injection CI, a signed artifact and MASTER-approved cross-Guardian integration. No change to active Preview, PG5432 or running services.

### Suggested read-only Windows inventory (manual, no mutation)

```powershell
Get-CimInstance Win32_Service | Select-Object Name, State, StartMode, PathName, StartName
Get-Volume | Select-Object DriveLetter, FileSystem, Size, SizeRemaining
Get-Process | Where-Object { $_.ProcessName -match 'postgres|tester' } | Select-Object ProcessName, Id, Path
```

Do **not** run `cleanup_temp`, `Remove-Item`, `Stop-Service`, `Uninstall`, `VACUUM FULL`, `pg_resetwal` or overwrite data directories. Restore is a separately authorized and verified operation, not part of this preflight. Missing evidence means **NO-GO**.

## ADMINIT full-uninstall contract: Storage-owned review criteria

Reference: `docs/guardians/adminit-desktop-install-uninstall-contract.md` at foundation commit `13d26b74`. MASTER #88 owns installer, destructive uninstaller, UI, service control and integration. **Storage Guardian never invokes uninstall or deletion.** This review contract does not alter the user's explicit full-deletion intent; it limits destruction to independently attested TESTER-owned targets after the exact typed `DELETE TESTER` confirmation and second destructive warning.

Independent disposable-fixture negative acceptance for MASTER's installer:

1. **Manifest validation:** absent, malformed, duplicated, stale, tampered, relative, traversal (`..`), drive-root, Program Files, mixed-case/8.3 Windows aliases, UNC/device/extended-length paths and alternate data streams must fail closed before any mutation. An apparently valid absolute string is not proof of ownership.
2. **Junction/reparse/symlink:** refuse linked install root, data root, any ancestor and each deletion target. Swap a verified directory for a junction immediately before deletion (TOCTOU) and assert foreign sentinel survives. Pathname `lstat` alone is insufficient; MASTER must provide handle-based identity checks.
3. **Foreign resources:** inject an unrelated service with a similar prefix, different ImagePath, unrelated scheduled task, desktop shortcut, database cluster and PostgreSQL 18 on 5432. None may be stopped, unregistered or modified.
4. **Destructive scope:** after *two* confirmations including exact `DELETE TESTER`, a disposable owned-only fixture should be fully removed, including owned candle history, results, dedicated DB, configs and any TESTER-owned backups/archives. No backup or archive retention; no broad disk sweep or deletion outside verified ownership. This is a test expectation, **not** an executable action here.
5. **Partial failure:** inject access denied, locked file, stopped/absent service, mismatched cluster identity, power interruption and stale lock. Never report full success while targets remain; preserve a minimal failure receipt outside deletion root with no candle history, credentials or secrets.
6. **Concurrency and idempotency:** refuse simultaneous start/uninstall; repeat uninstall only for independently attested already-absent targets, without escalating scope or touching foreign paths.
7. **Evidence:** capture pre/post manifests, exact file/service identities, explicit user confirmation, each attempted target and failure status; independently verify PostgreSQL 5432 unchanged. Use disposable isolated Windows fixture only.

**NO-GO** until MASTER supplies independently executed Windows fault-injection evidence and exact-head CI. Storage Guardian's existing `inventory()` is read-only and is not an uninstaller authorization API.
