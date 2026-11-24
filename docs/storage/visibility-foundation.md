# Visibility foundation v1 — experimental and UNENROLLED

Authority: MASTER Issue2 comment6711209526, limited foundation only.
Migration007 adds `tester_visibility` (separate from `tester_storage`); it is not
applied at import or Store construction. The existing checksummed migration runner
applies it explicitly. No roles/default privileges or legacy read/write defaults
are changed, and no grants are added for this schema. Existing bootstrap's broad
`tester_storage` grants do not target `tester_visibility`. Administrator/default
privileges still require review before any enforcement claim.

`epochs` fixes protocol_version1/statusUNENROLLED/success_enabledfalse and
external_restore_trustUNVERIFIED with CHECK constraints. Labels are caller supplied,
not external trust proof; duplicate registration fails. Restored contents, UUIDs,
PGsystem_identifier and clock samples cannot enroll this epoch. `scope_generations`
is only a prototype counter. `mutation_events` is append-only; `capture_drafts`
can only be INCOMPLETE/disabled, with both capture_observed_by_ms and
receipt_publication_observed_by_ms constrained NULL. There is no receipt issuer.

`create_unenrolled_epoch(connect, epoch_id)` and
`experimental_mutation(connect, epoch_id, scopes, mutator_tables)` explicitly require
a factory returning a fresh dedicated autocommit psycopg mapping-row connection.
They own/close that connection. An idle check rejects existing transactions; the
session common gate is try-locked before BEGIN. Busy gates fail immediately for
caller retry with a fresh connection. All prototype scopes/epochs share ONE gate,
so cross-scope operations take one lock; scope counters are sorted/deduplicated.
The context's declared writes, each affected counter and the mutation ledger use
one transaction. Body/ledger errors propagate for rollback; gate releases after
transaction exit, and connection close is a final release safeguard. Callers must
not commit/rollback/change transaction or role themselves inside the block. No
network/lock timeout or connection pooling contract is invented by the helper.
This is not a read capture helper: no RR snapshot or successful manifest is issued.

## Complete current mutator inventory (all bypass enforcement today)

| Tables | Current entrypoints / effects to fence in a future epoch |
|---|---|
| instruments, instrument_metadata | Store.ingest and save_instrument; revisions affect normalization/interpretation |
| raw_records, record_sources | Store.ingest, replay/source changes; all confirmed raw/support membership |
| ingestion_runs, ingestion_run_jobs, ingestion_run_evidence, source_observations | Store.ingest/recover, including later links to existing raw/source/run/evidence |
| pagination_evidence, coverage_intervals, coverage_resolutions | ingest, _coverage, mark_range, recovery; full proof and resolution membership |
| quality_findings, finding_resolutions | mark_range, conflict recording, check_aggregate, recovery; late incidents and resolutions |
| download_jobs, ingestion_checkpoints | ingest, recovery_jobs, recover claim/retry transitions; job/checkpoint provenance |
| source_archives | register_archive; manifest registration and overlap evidence |
| derived_aggregates, audit_events | save_aggregate/check_aggregate, _audit and ingest; derived lineage/quality |
| lifecycle_objects, lifecycle_object_evidence, lifecycle_references | lifecycle register_object/reference_object; dependencies and classes |
| backup_verifications, backup_evidence | lifecycle record_backup; restore claims/evidence |
| lifecycle_scans, lifecycle_quarantine | lifecycle scan; late quarantine changes proof admissibility |
| lifecycle_plans | lifecycle plan; plan evidence membership |

MUTATOR_TABLES matches all 26 current tester_storage business tables in migrations;
a pure regression fails if a migration introduces an uninventoried business table.
Direct SQL/owner/superuser access, trigger changes, future UPDATE/DELETE/TRUNCATE,
COPY/import, DDL/migrations/schema_migrations, role/default privilege changes,
restore/clone and foundation-table edits are additional control-plane bypasses.
No table-name declaration proves the caller actually declared every affected scope.
The prototype deliberately cannot enroll or produce success even if all its own
calls cooperate. Legacy Store/Lifecycle callers do NOT automatically participate.
A later enforcement design must address shared run/job/evidence spanning scopes,
new/missing scope membership and atomic generation increments for every mutation.

## Bounds, negative facts and clocks

A durable capture's observed-by bound is separate from later receipt-publication
observation. A C quality snapshot containing a later receipt CANNOT inherit the
capture's earlier bound. Both fields stay NULL here. Trusted UTC upper bound plus
uncertainty is separate from conservative rounding or monotonic ordering; neither
is implemented. Restore requires a fresh trusted external epoch outside restored
contents; no local helper establishes that trust.

No negative fact inventory is yet enforced or captured. DATABASE_FACTS_ONLY cannot
certify physical archival bytes, out-of-band file changes, or unreported defects.
Source time, DEFAULT now(), task/read time and counter values do not fill gaps.
Production knowledge_time is unchanged UNAVAILABLE/null. No C mapping or provider
default is enabled. A later protocol needs separate technical review and real PG
interleavings, durable configuration checks and clock/publication validation.

## Verification boundary

Pure mock tests cover old transaction refusal, gate-before-BEGIN and unlock-after-
commit order, busy gate refusal, same-transaction generation+ledger, body/ledger
failure propagation, invalid/missing/enabled epoch refusal, bounded declarations,
complete table inventory, disabled schema scaffold. They do not prove PostgreSQL
rollback, lock contention, SQL migration execution, durability or enforced closure.
MASTER owns retained-PG migration idempotency/preservation, constraint checks,
late links/incidents/cross-scope interleavings and crash/rollback checks separately.
No physical database/service/UAC/ACL/global-setting/deletion action is part of B's
code-only stage. Old raw/source timestamps and normalized version1.0 are untouched.
