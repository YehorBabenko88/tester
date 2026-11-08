# B narrow lineage review

HEAD **e236395684d661f9464a62a0f6f67803d9000de6**, diff against **47fd36f32389174377682a13862f5b36c7a886da**. Read-only tester source review; no GitHub, fetch, product edits or PostgreSQL calls. Root supplied exact fetched object and snapshot `work/b-lineage-check`.

**Bounded approval: no material defect found in this correction.** This is approval of the changed lineage/control-flow contract, subject to root's separate real PostgreSQL run; not a certification of exchange provenance or full storage behavior.

- `Store.ingest` checks declared evidence.ingestion_run against a valid explicit caller run before publication. Declared record source.ingestion_run is checked after normalize inside the existing publication transaction, before writing that row. A late mismatch raises ValueError and unwinds the same connection transaction containing prior rows, run/job/evidence/checkpoint work; it is not caught as DataConflict and cannot accidentally produce the separate conflict-finding path. An absent declaration remains explicitly undeclared, not rewritten.
- `source_run_status` compares exact nonempty string declarations with the supplied run, rejects non-string/oversized/empty values, and distinguishes matched/mismatched/invalid/undeclared. Historical mismatched rows now produce authentic_run=false and aggregate authentic=false while retaining original source/raw_envelope. The term authentic_run still means B caller/run linkage under its documented trust boundary; it is not independent proof of source identity.
- Recovery chooses actual evidence.ingestion_run when declared, otherwise the existing recovery-token fallback. The claim attempt_token remains separate and is still passed through `_attempt_token`; mismatching record declarations reject transactionally. Empty/invalid declared evidence cannot silently fall back to a generated run.
- Existing publication fencing is unchanged: job row lock/request scope, current attempt-token ownership check, checkpoint monotonicity, session claim across fetch and claim connection liveness check. Late-failure retry UPDATE still requires matching attempt_token AND state=running; an obsolete failure cannot overwrite a newer committed job. Successful publication clears the token atomically. No new unlocked source/run reassignment or weakening of these predicates appears in the diff.
- New real-PG test fixtures cover matching declaration preservation, late mismatch after an otherwise valid row with table-count/checkpoint rollback, invalid declarations, historical mismatched old data and recovery's true acquisition run. Those test bodies were read, not independently executed against PostgreSQL here.

## Independently executed

`git diff 47fd... e236... --stat` / source diffs: six changed files; production changes limited to store.py and provenance.py, no migration/schema mutation.

Initial `../venv/Scripts/python.exe -m unittest discover -v` without workspace temp setup: 57 methods, 44 PG skips, one sandbox Temp PermissionError; incomplete green claim avoided. Log: `work/b-e236-independent-unit.log`.

Rerun with `tempfile.tempdir=os.getcwd()` and unittest discovery: **57 discovered, 13 pass, 44 PostgreSQL-dependent skipped, exit0**. Log: `work/b-e236-independent-unit-workspace.log`. These counts are not 57 executed PostgreSQL passes.

An inline isolated probe exercised actual `source_run_status` for undeclared/matched/mismatched/None/bool and actual `resolve` with a mocked SQL result representing historical source.run=old / caller.run=new. All five status cases passed; resolver returned authentic=false and preserved old source_ingestion_run. This verifies Python resolution behavior, not SQL joins/commit/rollback.

Root owns the independent real PostgreSQL57 suite. No author PG log was treated as this reviewer's execution; no claim about that run's outcome is made here.
