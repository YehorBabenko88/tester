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


## DATA↔RESEARCH conflict resolved — MASTER 2026-11-14, current source pins

As of DATA HEAD `93d13a8e78ff7c2aacc6610f838732ba22db8234`, reviewed RESEARCH #71 HEAD is
`c01c32b03aaf04923b33b0513b71776b12af1e09`.
Direct comparison of the two Draft changed-file lists showed **exactly one
overlapping path**, `tests/platform/test_research_postgres.py`.
The path is now **byte-identical** on both producer heads:
Git blob `402c25410ba4cc6704911148b1ec0c5bba720088`.
The prior variants both checked the client-side libpq destination, but
DATA additionally poisoned `PGHOST`, `PGPORT`, `PGDATABASE` and
rejected explicitly foreign hostaddr; both writers independently converged
on that strict test. No production `research_ledger` source was changed.

**MASTER composition rule:** adopt the single common blob by exact Git
identity, not by "last writer wins"; reject any future head whose
`tests/platform/test_research_postgres.py` hash diverges. Keep the
post-merge regression verifying `conn.info.host`, `conn.info.hostaddr`,
`conn.info.port`, `conn.info.dbname` against the validated loopback DSN.
`inet_server_addr()` and `inet_server_port()` report the PostgreSQL
server's Docker interface (e.g. 172.x:5432), not the runner's published
127.0.0.1:55472 endpoint. Host-side and server-side values must not be
equated. The negative tests for explicit `127.0.0.2` / `172.18.0.2`
and inherited `PGHOSTADDR` redirection remain mandatory.

**Exact files MASTER needs from DATA #72:**

- Runtime DATA inputs: `src/ingest/execution_contract.py`,
  `src/ingest/public_auxiliary.py`,
  `src/ingest/public_auxiliary_runner.py`,
  `src/storage/models.py`,
  `src/storage/migrations/008_public_unknown_taker_side.sql`.
- Unchanged common storage dependencies already present on base:
  `src/storage/migrations.py`, `src/storage/store.py`,
  `src/storage/provenance.py`, `src/storage/migrations/001..007`,
  and `src/quality/candles.py`. Preserve their source identities and
  the canonical 008 SHA.
- DATA verification: `tests/storage/test_postgres.py`,
  `tests/test_public_auxiliary.py`,
  `tests/test_public_auxiliary_postgres.py`,
  `tests/test_public_auxiliary_runner.py`,
  `tests/test_execution_contract_bridge.py`,
  `tests/test_execution_contract_postgres.py`,
  `tests/test_composed_data_research_postgres.py`,
  `tests/test_live_public_bybit_postgres.py`.
- Shared merge overlap: `tests/platform/test_research_postgres.py`
  (canonical common blob above; **copy only once**).
- CI definitions/evidence templates: `.github/workflows/storage.yml`,
  `.github/workflows/public-auxiliary-data.yml`.
  On the composed candidate, replace producer workflow branch references
  with immutable candidate/source SHA attestations and preserve independent
  PG14/18 jobs and live-bybit manual opt-in. Do not turn on live calls.
- Documentation: `docs/ingest/EXECUTION_EVENT_CONTRACT_V1.md`,
  `docs/ingest/PUBLIC_AUXILIARY_DATA.md` and this coordination report.

**Independent RESEARCH source inputs:** keep
`src/integration/data_research_adapter.py`,
`src/analytics/research_metrics.py`,
`src/integration/research_preview.py`,
`src/integration/research_ui_projection.py` from exact RESEARCH #71 HEAD
`c01c32b03aaf04923b33b0513b71776b12af1e09`.
Only the explicitly reviewed `tester-public-execution/1` contract,
`Unknown` taker semantics and source-original HTTP receipt verifier may
feed observed metrics; UNKNOWN historical completeness remains immutable.
UI #74 and OPS #73 must be independently pinned by MASTER.

Candidate #77 is deliberately **not modified** by DATA. Actual composition
must happen in a new isolated staging HEAD and pass byte-identity,
migration checksum, PG14+18, cross-module tests, full Python, Node,
Chromium and security gates before any release or physical deployment.
