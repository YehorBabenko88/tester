# Run provenance, fenced recovery and lifecycle dry runs

Implements PR #13 comment 6708160404 and initial non-destructive Issue #14 comment 6708160604. Core schema version is migrations 001–006; normalized envelope remains 1.0. Run migrations before starting the new writer. Re-run bootstrap to grant the runtime role access to added tables. No user database is migrated automatically.

## Write-time provenance contract for A/E/MASTER

```python
store.ingest(records, evidence, job_key, checkpoint=None, run_id=actual_acquisition_run_id)
```

`run_id` is an actual acquisition identity supplied by A/E at write time (1–300 characters), stable across retries belonging to that run. A new independent acquisition supplies a different ID. For explicit runs every record requires `source.source_kind` in `rest`, `archive`, `import`. `timestamp_precision` retains actual s/ms/us/ns precision as before. Source content and raw envelopes remain immutable; a source is deduplicated as content, but **each observation** is linked to run/job/evidence with its own deterministic ID. Same-source replay in two runs yields two observations and one raw record. A run can span multiple jobs. EMPTY/PARTIAL/FAILED acquisition evidence has independent run/evidence links even with no records; EMPTY still never verifies history.

Migration 005 adds `ingestion_runs`, `ingestion_run_jobs`, `ingestion_run_evidence`, `source_observations` and relational FKs including evidence/job scope consistency. Historical source rows do not acquire invented runs on migration or read. Legacy `ingest` callers omitting run_id receive a clearly tagged `legacy_job` write-time relation; this is compatibility bookkeeping, **not authentic externally supplied run provenance**. `legacy-job:` namespace is reserved. Existing source_kind-unknown data is reported honestly.

Read interfaces:

```python
by_key = store.provenance(record_keys)  # at most 10000 keys
proof = by_key[record_key]
# proof: observations[], evidence_refs[], run_ids[], unlinked_sources[], authentic
# each observation: observation_id, source_id, source, raw_envelope, run_id,
# job_key, evidence_id, source_kind, timestamp_precision, pagination_status,
# verified, evidence (full request/pages/checks/fingerprint), validator_version,
# authentic_run (caller-supplied vs legacy_job)

rows = store.read(dataset, venue, category, symbol, start_ms, end_ms, interval='', limit=1000, after=None)
# Every row attaches `provenance` above and `sources` for all observations.
# Old single `source` field is compatibility-only; it is not an exhaustive resolver.

evidence = store.coverage_evidence(venue, category, symbol, dataset, interval, start_ms, end_ms)
# Each overlapping ledger entry: evidence_id, job_key, start_ms, end_ms,
# status, verified, validator_version, evidence, run_ids[], observation_refs[].
# Run IDs also exist for empty acquisitions. This API returns audit evidence,
# not an implicit statement that every returned historical proof is currently valid.
```

Use coverage state plus actual evidence refs, observation authenticity and current findings/file verification. Do not promote the boolean COMPLETE to an E proof by itself. No run is created on reads. This package does not implement E's HTTP envelope or duplicate C's mathematics; MASTER owns `src/integration/storage_api.py`.

## Recovery claims

`Store.recover` retains the previous fetcher callback signature. A dedicated connection acquires an exclusive **session advisory lock per recovery job** before the fetch and holds it until publication/failure. Nonowners skip fetching. The durable `attempt_token` changes at claim time and is passed to fetcher as `job['attempt_token']`; each recovery ingestion has a write-time `run_id='recovery:'+attempt_token`. Token checks fence publication under the job lock. Errors use conditional `UPDATE ... WHERE attempt_token=... AND state='running'`; a delayed failed attempt cannot change a replacement's committed result. Successful commit clears token and old error code atomically.

Connection loss/process death releases the session claim; a later worker can acquire a new token. A still-connected hung downloader keeps the job leased: the acquisition adapter must enforce network/cancellation deadlines. There is deliberately no wall-clock lease expiry that would start a conflicting live fetch. The regression test terminates the old claim backend, lets a replacement verify/commit, then releases the old failure and asserts state remains committed. Another test proves only one concurrent fetch executes.

## Lifecycle API — Issue #14 first delivery

```python
from src.storage.lifecycle import StorageLifecycle
lifecycle = StorageLifecycle(store, existing_archive_or_cache_root)
lifecycle.register_object(object_id, retention_class, path, evidence_refs)
lifecycle.reference_object(parent_id, child_id, evidence_refs)
plan = lifecycle.plan()  # persisted plan_id, DRY_RUN, policy, objects, estimated bytes
metrics = lifecycle.metrics()
scan = lifecycle.scan(max_records=10000)
gate = lifecycle.acquisition_gate(min_free_bytes=1024**3)
```

Classes: RAW_PROTECTED and VERIFIED_ARCHIVE always protected; DERIVED_REBUILDABLE/CACHE/TEMP only become **dry-run candidates** if their content is intact, references permit it and a still-present verified backup covers all their source evidence. Registered paths have a unique OS-canonical identity and cannot be reclassified via another object ID. Known source-archive paths remain protected even if misdeclared as cache. Classification/content identity is immutable. A completed backup's evidence-reference set is immutable, not extendable to later runs. Plans are informational; `execute(plan_id)` unconditionally raises PermissionError. No raw/source deletion, physical quarantine/move, VACUUM FULL, WAL pruning, partition detach or user DB mutation is implemented.

`scan` reads a repeatable-read snapshot, bounds raw-source/evidence inspection, validates raw identity/content/precision against original envelopes, source identity hashes, missing source/run evidence, request pagination chains, registered file/archive SHA/size and records logical quarantine findings. It does not modify raw data. Archive checks perform full file hashing and can be I/O-intensive; the raw-record bound is not a total file-byte budget. Quarantine has no automatic removal; an explicit future resolution policy is required. A scan is not a certificate of cryptographic collision absence or complete exchange history.

`metrics` reports per-table relation bytes and explicitly approximate row counts from pg_stat_user_tables, last autovacuum/autoanalyze, WAL bytes when privileges allow (otherwise reason+NULL), local configured-root free disk, last scan, backup RPO/RTO and checkpoint/WAL/autovacuum settings. It performs no automatic VACUUM/ANALYZE or configuration changes. Monitoring a remote server's actual disk needs an external agent; the local disk figure applies to the configured local root only.

Disk guard: configure `Store(dsn,storage_root=existing_root,min_free_bytes=...)` or TESTER_STORAGE_ROOT. Low disk prevents recovery fetch and ingestion publication, returning `paused_low_disk`/raising AcquisitionPaused; confirmed data is retained. `acquisition_status()` is the E worker pre-acquisition gate. Unconfigured legacy stores report UNCONFIGURED and cannot claim disk monitoring. E/A must configure the root and call the gate before their own independent download path. `guarded_fetch` is a convenience for those paths.

Backup attestation API:

```python
lifecycle.record_backup(backup_id, dump_path, restored_evidence_refs, restored=actual_restore_passed,
    data_cutoff_ms=last_committed_data_time, backup_start_ms=..., backup_end_ms=...,
    restore_start_ms=..., restore_end_ms=...)
```

This persists actual caller-performed restore-drill results with dump hash/size, monotonic wall-time chronology, RPO=backup_start−data_cutoff and RTO=restore_end−restore_start. Setting restored=True is a trusted operational attestation, not something inferred from a checksum. The automated drill actually invokes pg_dump and pg_restore into a new disposable DB, checks raw/checkpoints/source observations and migrations, runs a concurrent integrity scan, and records measured timings. External object restoration and production-role remapping remain separate obligations.

Local validation before publication: 51 tests, 11.086s, OK, no skips on PG18.4/Python3.14 Windows. Separate B+main compatibility snapshot:60 tests/11.066s, OK, zero skips (no merge). Logged branch synthetic metrics: raw table 122880 bytes, WAL 33554432 bytes, snapshot scan 33ms with one intentionally corrupted cache finding; 77742-byte dump, measured RPO54ms/RTO225ms. These are small-fixture observations, not production capacity or RPO/RTO guarantees. Final exact HEAD and logs are reported in PR/Issue comments.
