# DATA #72 migration 008: safe integration plan for MASTER candidate #77

**Status: STAGING_ONLY / NO_GO**. Contract source is [Draft PR #72](https://github.com/YehorBabenko88/tester/pull/72);
MASTER candidate is [Draft PR #77](https://github.com/YehorBabenko88/tester/pull/77).
No mutation of main, PC4, installed PostgreSQL 5432, or trading services.

## Exact producer identity and migration exclusivity

The canonical migration is `src/storage/migrations/008_public_unknown_taker_side.sql`.
At reviewed DATA HEAD `03af5d7a80107f5ea7e4465df8e5e37263ad5474`, its Git blob SHA is `05600c50e3d4598aaa4645ac58e137a719b0eed2`.
MASTER must pin the **exact source SHA** in a new disposable candidate and verify the
migration content/SQL checksum before running any migration. Do not recreate
another numbered `008_*.sql` from RESEARCH: duplicate migration numbers collide
under `src.storage.migrations.migrate` and can invalidate replay checks.

Candidate #77 HEAD `c8cfcb76d0d92028fee8e254a66732442ed184f3`
was inspected and did **not** contain DATA's canonical 008. Merely including
staging documentation is not a migration deployment.

## Disposable acceptance sequence

1. On an isolated CI runner, check out a named immutable DATA SHA and a named
   immutable MASTER candidate SHA; compare the exact 008 Git blob and reject
   duplicates, diverged SQL checksums, missing predecessor migrations 001–007
   and unexpected later migration numbers.
2. Provision only a **dedicated, freshly initialized disposable** PostgreSQL 14
   or 18 service. Never point the test DSN to installed 5432/PC4. Check the
   database identity and migration table before allowing schema writes.
3. Reconstruct a true v1-format raw trade, original `record_sources` and
   provenance (including exact Decimal strings, raw envelope and source hashes).
   Record the pre-upgrade value of `raw_records` and `record_sources`.
4. Apply 002 through 008 in one transactional and checksummed migrator run.
   Confirm schema head=8 and the exact migration checksum. Verify all
   historical rows, trade IDs, source JSON and content hashes match the
   original pre-upgrade snapshot; provenance that was unlinked must remain
   **unlinked** rather than being silently upgraded to authentic.
5. Require both `raw_records_check2` and `trade_side_required` to accept
   the source-native enum **Buy/Sell/Unknown** without changing any side.
   Check price>0, size>0, stable trade ID and source scope still apply.
   Probe valid Unknown and invalid Other/zero-size/blank-ID only inside
   rollback-only SQL transactions; no probe rows may survive.
6. Replay the exact recorded original Bybit HTTP bytes through DATA's
   public execution writer into the disposable DB. Reconnect using a fresh
   `Store` and verify original HTTP SHA256, canonical page/row receipts,
   immutable `execId`, Decimal and provenance. Repeated identical ingestion
   must remain idempotent; conflicting economic payload must be rejected.
7. Compose the exact RESEARCH #71 source **only in the isolated CI runner**
   and run its `read_data_metrics` negative/positive cases. Unknown taker
   side contributes to volume/VWAP/POC but **never** to known Buy/Sell Delta
   or CVD. `source_coverage=UNKNOWN`, `publication_ms=None` and
   `admitted_to_f=false` remain invariant.
8. Run PG14 and PG18 independently (no fail-fast), plus B→E API and
   source-integrity acceptance; archive exact SHA, migration checksum,
   stdout/stderr, CI job IDs and negative-result evidence before any
   release discussion.

## Incident/rollback policy

Do not automatically roll back a successful irreversible/append-only source
upgrade on shared installations. A failed disposable transaction must leave
the pre-upgrade data unchanged; discard the disposable database after CI.
On an existing DB with migration 008 recorded but an unexpected checksum,
fail closed and require owner review rather than editing
`tester_storage.schema_migrations` or historical `raw_records`.

The host-side PGHOSTADDR test must compare the **client's pinned numeric
destination** (`conn.info.hostaddr`, `conn.info.port`, `conn.info.dbname`)
against its validated DSN; `inet_server_addr()/inet_server_port()` expose
the server's container interface (typically 172.x:5432), not the published
GitHub runner loopback port. Continue to reject explicit foreign hostaddr,
environment redirects and default or foreign databases.

## Access and release boundaries

Live public Bybit HTTP 403 remains `ACCESS_BLOCKED`; do not proxy around it,
fabricate source coverage or claim historical completeness based on stored
recent-trade pages. No real trades, no automatic deployment. **NO_GO**
until full exact-source CI acceptance and independent MASTER authorization.
