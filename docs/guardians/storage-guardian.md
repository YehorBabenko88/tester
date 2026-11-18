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
