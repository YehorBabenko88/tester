# B heartbeat 1 independent review

Frozen B PR #13 head: **47fd36f32389174377682a13862f5b36c7a886da**, isolated exact git-archive snapshot work/b-heartbeat1. Reviewed new provenance/run links, recovery claims/CAS, lifecycle dry-run and E adapter binding. No product/remote edits; scratch adverse regression only.

## Recovery follow-up: initial finding withdrawn

An initial static P1 theory claimed unverified repair committed its job before the recovery retry CAS. **Withdrawn:** recovery injects nonempty finding resolutions and Store.ingest rejects an unverified resolution inside the transaction, before the job's final committed update. That failure rolls back raw/evidence/checkpoint publication, leaves the matching running attempt for the retry CAS, and correctly schedules another fetch. The original static review omitted this important control path. No B product fix is justified for that theory.

Scratch expected-correct regression: work/b-heartbeat1/adverse_recovery_probe.py. It uses B's actual PostgresTests randomized isolated DB setup and synthetic fixtures, first returns a valid PARTIAL record, then offers verified complete data. Root's initial actual PostgreSQL run PASSED: first retry/ValueError, second verified, both fetches occurred. Fixture already included source_kind='archive'. Strengthened probe additionally sets that source kind explicitly and asserts first attempt leaves zero raw/evidence and no checkpoint, proving expected atomic rollback rather than manufactured partial commit. Root will execute strengthened probe; this agent did not start PG.

## Resolved gates and binding

- Migration 005 appends ingestion_runs, ingestion_run_jobs, ingestion_run_evidence and source_observations with relational FKs and immutable triggers. Explicit caller run identity is distinct from legacy-job identity. No migration manufactures historical run assignments. Explicit new runs require known source_kind. Source replay in two caller runs stores two observations even when source_id/raw content is shared. Provenance resolver explicitly marks legacy/unlinked or unknown-kind observations unauthentic. This resolves the old missing-link design for new authentic writes; old history still correctly cannot be certified automatically.
- Store.provenance(record_keys) returns a dictionary by record_key. Each item has observations, evidence_refs, run_ids, unlinked_sources, authentic. Each observation includes source/raw_envelope, source_kind, precision, run_id/job_key/evidence_id, pagination status/verified/evidence/validator_version and authentic_run. Store.read now embeds that item as row.provenance and adds sources; row.source remains an arbitrary first source for compatibility. **Factory must use the observations, never row.source alone.** Require item.authentic and no unlinked_sources; use observation.run_id for ingestion_run. Map rest→bybit_rest and archive→bybit_archive. The storage import kind has no universal honest shared-contract mapping; require an explicit import policy, not an assumed synthetic label.
- Preserve exact raw-provided source timestamp/precision. ns/ms/us fields remain B's source values; shared trade adapter's us→ns string scaling preserves existing microsecond precision. A run ID must never be newly generated while reading. Observation.source.url/sha256 supply endpoint/hash, with missing source hash kept null.
- Store.coverage_evidence(venue,category,symbol,dataset,interval,start_ms,end_ms) returns a list of overlapping pagination-evidence objects with evidence_id,job_key,start/end,status,verified,validator_version,evidence,run_ids,observation_refs. **This is a historical evidence list, not an active complete coverage result.** Use Store.coverage for active findings/resolutions/archive integrity, then attach verified covering evidence IDs conservatively; PARTIAL/old/failed observations must not certify COMPLETE. Partial overlap alone does not cover the full request.
- Session-level recovery claim now spans network fetch; a persistent token fences publication. Late failure UPDATE uses matching token AND running state, preventing an obsolete worker from reverting another successful claim. New tests target duplicate concurrent fetch and connection-loss takeover. The P1 above is a distinct nonverified-own-publication path not covered by these tests.

## Lifecycle #14 assessment

New lifecycle API implements all five classes, immutable registered identities/path classifications, reference/backup gates, no destructive execute, archive/file hash checks, snapshot scan, quarantine, per-table/WAL/free-space/last-scan metrics, backup RPO/RTO attestation records and optional acquisition disk gate. RAW_PROTECTED/VERIFIED_ARCHIVE are excluded from dry-run eligibility. Database tables and archive bytes remain untouched by execute, which raises PermissionError. This materially resolves the earlier entirely-missing lifecycle implementation gap, but is still a non-destructive first slice, not full #14 acceptance.

Remaining important limitations: scan(max_records) always scans the same first source_id prefix, has no continuation cursor, and caps max_records at100000; repeated bounded scans cannot progressively inspect a larger history. Its lifecycle object and archive checksum loops are unbounded by max_records, so one scan may hash every archive byte. Gap endpoints/coverage completeness and source-observation/evidence linkage contents are not comprehensively scanned. No integrity scan concurrent with backup drill is tested. Store with no configured storage_root returns allowed=True,state=UNCONFIGURED, so deployment must explicitly configure the gate; it does not automatically monitor PostgreSQL's data/WAL disk. No destructive policy exists, so these are incomplete integrity/monitoring scope rather than observed deletion.

## Verification

Executed own non-PG tests with workspace TEMP/TMP and TESTER_TEST_ADMIN_DSN unset: **51 methods, 13 passed,38 skipped, zero failures**, log work/b-heartbeat1-unit.log. Root subsequently reported **51/51 actual PostgreSQL tests passed** and the initial adverse probe passed; the stronger rollback-specific assertions await its follow-up run. No sockets/server restarted by this agent.

Exact root commands from work/b-heartbeat1, with PostgreSQL18 bin on PATH, TEMP/TMP workspace directory, TESTER_TEST_ADMIN_DSN='host=127.0.0.1 port=55449 user=acceptance_admin dbname=postgres connect_timeout=5':

    ../venv/Scripts/python.exe -m unittest discover -v
    ../venv/Scripts/python.exe -m unittest adverse_recovery_probe.AdverseRecoveryProbe -v

Sources: https://github.com/YehorBabenko88/tester/pull/13 ; immutable https://github.com/YehorBabenko88/tester/tree/47fd36f32389174377682a13862f5b36c7a886da .
