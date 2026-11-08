# Independent visibility foundation review — 70a3

Reviewed exact tester commit 70a3ae5f82f707365c17048b2d58379a8e2456c4 against proposal-only parent 89221f2ecc20827e9d15e40de6e39ab51c0456d0. GitHub compare and local Git diff show seven changed files: new migration/helper/tests, foundation documentation and test-result notes, and the existing migration-count expectation. No Store/read/provider/C binding or legacy writer implementation changes.

## Narrow assessment

No new material correctness defect found for the explicitly UNENROLLED prototype scope. This matches the limited foundational stage previously recommended; it does not establish enforced visibility, historical success, trustworthy UTC, or a receipt issuer. Keep production knowledge_time UNAVAILABLE and all consumer mappings disabled.

Migration007 creates a separate tester_visibility schema. epochs fixes status=UNENROLLED, success_enabled=false and external_restore_trust=UNVERIFIED with NOT NULL/CHECK constraints. capture_drafts fixes state=INCOMPLETE, success_enabled=false and both capture/receipt visibility bounds to NULL. There is no successful receipt table or callable enrollment transition. Existing tester_storage tables/defaults/roles are not altered and no GRANT/default-privilege statements are introduced. Append-only triggers protect epoch/event/draft history; scope generations are intentionally mutable prototype counters. Existing administrator/default privileges are not proven safe merely by absence of GRANT in this migration and still need actual role inspection.

The helper rejects non-autocommit or non-idle connections before issuing its gate query. pg_try_advisory_lock acquires one global session gate before c.transaction begins; busy acquisition does not start the mutation transaction. All epochs/scopes share this gate, so it does not omit a discovered per-scope lock or introduce cross-scope ordering differences. Scope identifiers and table declarations are validated/bounded, sorted and deduplicated. Inside the one READ COMMITTED transaction it checks the disabled epoch, increments each scope generation, yields the declared writes and appends the operation ledger. Normal body/ledger errors escape the transaction context for rollback. Unlock happens after transaction exit; closing the owned connection is the final release path even if unlock fails.

READ COMMITTED here is intentional for the prototype mutation helper; this is not the proposed observer's fresh RR capture path. No RR capture API or publication clock is implemented, so the code does not yet prove the observer protocol.

## Remaining boundaries before any stronger claim

- This gate is cooperative. Existing Store/Lifecycle methods, direct SQL, owner/superuser/maintenance paths and control-plane changes bypass it. The 26-table inventory is complete for current tester_storage business tables, but declaration of table/scope names does not enforce that those are the only affected objects. schema_migrations, roles/default privileges, trigger changes and restore lineage remain explicit control-plane exclusions.
- A dedicated connection is a caller precondition. An idle reused connection cannot retain an active MVCC transaction, but the helper cannot authenticate that the factory never hands the same live connection to another thread. Caller must not issue raw COMMIT/ROLLBACK/role changes or release the session gate inside the block. None of those limitations are hidden behind success: epochs cannot enroll.
- The mocks check control ordering and propagation, not actual PostgreSQL rollback, lock contention, SQL constraints, migration idempotency, role permissions or durability. Real PostgreSQL body failure, ledger failure, busy gate and connection-loss tests remain required before claiming those properties tested.
- Connection establishment/SQL/network waits have no new timeout contract inside this helper; the global try-lock itself avoids a blocking gate wait. Do not infer bounded complete execution merely from try-lock.
- Capture materialization, negative predicates, observed visibility versus later receipt publication, trusted UTC uncertainty, external restore epoch and immutable archive lifetime remain unimplemented. Neither DEFAULT now nor prototype generation counters fill those gaps. Legacy rows are not given invented earlier knowledge times.

## Executed evidence

Loaded exact helper/test source directly from Git objects into memory; did not extract or edit product files. Ran **11 mock tests, all PASS**, covering gate-before-BEGIN/unlock-after-commit, stale transaction rejection, busy gate refusal, body/ledger failure propagation, sorted generations, disabled epoch checks, declaration caps and connection cleanup.

Separately checked exact Git migration text for the four disabled-state/NULL-bound constraints, no GRANT or existing tester_storage alteration, and derived the business-table inventory from all exact-head migration blobs: **26 tables match MUTATOR_TABLES**. These substitute the two test methods that otherwise read the current checkout's migration directory, avoiding accidental mixed-version evidence.

No PostgreSQL server, SCM, ACL, elevation, physical database/root, new worktree, branch/staging or source-code actions occurred. Only this report was written. Actual PostgreSQL foundation verification remains ROOT's next-cycle work; this reviewer does not claim it passed.
