# Opt-in market catalog runtime

The default API does not mount the catalog or create database objects. Existing
download/job handlers and their measured counters are unchanged.

An operator must explicitly set `TESTER_MARKET_CATALOG_DATABASE_URL` to a dedicated
loopback PostgreSQL database on a non-5432 port, run
`python -m src.api.catalog_store` once to apply the separately checksummed catalog
migration, and set `TESTER_MARKET_CATALOG_ENABLED=true` for the candidate API.
The regular `TESTER_DATABASE_URL` may independently point to that same owned test
database; there is no fallback between the two settings. Startup only checks the
catalog tables, including migration 002's observation table, and fails if migration
was omitted. Re-running the explicit migration applies new checksummed versions;
startup never applies them. Do not use the installed native
database or service configuration for this opt-in feature.

Each category has a dedicated session advisory lock. Only that session's service
can acquire and atomically publish its snapshot plus attempt/backoff metadata.
Other API processes serve the committed PostgreSQL snapshot and enqueue refresh
requests in the same database. Lost sessions cannot commit; another service takes
ownership without resetting persisted retry timing. Shutdown cancels cooperatively,
drains public HTTP work and releases ownership. The last successful snapshot is
retained on failure. Storage retains one bounded snapshot per category (32 MiB
application publication budget, 64 MiB database serialization ceiling), not an
unbounded snapshot history. Genuine fresh price/OI ticker observations are stored
separately with a unique category/symbol/source-timestamp key. Publication prunes
only this feature's observations older than 26 hours and caps each category at
500,000 points, retaining the newest source timestamps first. Missing historical
metrics remain unavailable; a restart does not manufacture a baseline.

Price change 1h and OI changes 1h/24h use `(current - baseline) / baseline` as a
Decimal ratio, with a strictly positive baseline and exact source/unit matches.
The current observation must genuinely be OBSERVED with source and local observation
timestamps less than 300 seconds old. The baseline must be at or before the requested
horizon target and at most one 300-second cadence older. Every result includes the
baseline source time, actual elapsed window, source, original unit/value and current
value; the result unit is `ratio`. Null, stale, future, missing-unit, changed-source,
zero-denominator or absent history never becomes an invented delta. Raw observations,
qualified derived metrics, snapshot revision and attempt/schedule metadata commit in
one owner-session transaction. A successfully exhausted catalog can publish while
tickers fail; matching prior metrics retain their original timestamps with STALE
state, and the failed ticker attempt still invokes backoff.

Normal automatic refresh interval is 300 seconds plus 0–15 seconds jitter. Manual
requests coalesce and respect a 30-second success budget and persisted exponential
failure backoff (600, 1200, then 1800 seconds). Browser reads do not refresh sources.
Catalog acquisition, ticker provenance and unavailable historical metrics remain
distinct in the snapshot contract; the snapshot commits atomically only after its
required source requests succeed.

`GET /api/market-catalog/{linear|inverse}` accepts `search`, `status` (default
`Trading`, empty for all), `contract_type`, `contract_kind` (`perpetual`, `dated`,
`unknown`), `settle_coin`, repeated `watchlist=category:symbol`, `sort`, `direction`
(`none`, `asc`, `desc`), `offset`, `limit` (1–500), and optional `revision`.
All filters and sorts operate on the full committed snapshot before pagination;
numeric nulls sort last and symbol/category break ties deterministically. An empty
watchlist means no watchlist filter. A changed pagination revision returns 409.
The existing schema-version/data envelope and per-metric provenance are preserved.
ETags include freshness, failed attempts and schedule/pending state.

`POST /api/market-catalog/{category}/refresh` returns 202 with
`{"schema_version":"1.0","data":{"category":"linear","queued":true}}`.
This acknowledges a coalesced request, not successful source acquisition. The
existing same-origin write and trusted-host policy applies unchanged.

`catalog.next_refresh_at` and `last_success` are Unix seconds for compatibility.
`catalog.next_refresh_at_ms` is explicitly Unix milliseconds;
`refresh_interval_seconds` is 300. The actual retry deadline includes backoff.

Optional real database tests require an explicitly supplied disposable
`TEST_CATALOG_DATABASE_URL`; the suite does not start or migrate any native
PostgreSQL installation. Tests include a second process attempting ownership,
session termination/failover, restart reads and stale snapshot retention.
