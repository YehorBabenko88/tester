# Public auxiliary DATA — experimental isolated adapter

Source: Issue #58 DATA handoff (2026-11-13), pinned application base
`3b2eef8a4139195db1a775545e83f1c1f58bf403`.

## Contract and limits

- Public Bybit V5 only: `/funding/history`, `/open-interest`,
  `/recent-trade`; no private credentials, no identified trader data.
- Exact original decimal strings, exchange event/observation timestamps (ms),
  observed retrieval time and explicitly unknown publication time.
- HTTP body SHA256, URL, status, parsed-result equality and original row
  SHA256 are required before storage publication.
- Every page is atomically written with a caller-origin ingestion run, immutable
  source observation and pagination evidence through `Store.ingest`.
- **Historical coverage is always UNKNOWN.** A returned page, terminal cursor,
  or short funding response does not prove complete market history.
- Recent trades are a bounded *current* public snapshot, not historical
  execution coverage; no inference of order book, maker identities or positions.
- Funding uses a bounded reverse-time window; OI uses bounded cursor traversal.
  Replays must preserve exchange identity and content hashes.

## Experimental operator-only runner

`python -m src.ingest.public_auxiliary_runner --kind open_interest --category linear --symbols BTCUSDT --interval 5min --start-ms <start> --end-ms <end> --max-pages 2`

Requires `TESTER_AUX_DISPOSABLE_DSN` pointing to an **already created,
migrated, dedicated, limited-role** loopback database named
`tester_aux_disposable_*` on a port other than 5432 or 55471.
No database, role, service or machine is provisioned automatically. Do not
run against PC4 or existing installations. Only an authorized isolated
operator should run this command. At most ten unique symbols are admitted.

Run local offline tests:

`python -m unittest tests.test_public_auxiliary tests.test_public_auxiliary_runner -v`

Disposable PostgreSQL CI test:

`python -m unittest tests.test_public_auxiliary_postgres -v`

The PostgreSQL test requires `TESTER_AUX_TEST_DSN` and an empty disposable
database. The GitHub workflow provisions its own PostgreSQL 16 service.

## Outstanding release gates

The adapter is **not wired to the default platform worker/API**, has not
demonstrated live one-then-ten Bybit acquisition or completed CI acceptance,
and has not established complete funding/OI/trade historical visibility.
The isolated runner is a separate opt-in research entry point, not a
production scheduler. Release state: **NO_GO**.
