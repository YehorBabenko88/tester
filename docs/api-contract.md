# API contract v1

## Approved Issue #8 boundary (supersedes the initial simplified record shapes)

`contracts/PIN.json` pins the unchanged shared package to coordinator commit
`9766c0093e2608280875ac56db074414a3d62c69`, with SHA256 hashes. Market records and
dataset envelopes are validated with shared `validate_record`/`validate_response`
both before returning from the provider boundary and during FastAPI serialization.
OpenAPI record definitions are expanded directly from that schema, rather than
maintaining another set of financial fields. Root models preserve all accepted
fields exactly. Unsupported fields/versions are rejected, never silently dropped.

Every dataset response now requires all of: `schema_version`, `data`, `next_cursor`,
structured `availability` (including reason), `coverage` (including evidence refs),
and provenance array. Providers must supply the full envelope; E does not invent
metadata defaults. Source financial strings stay exact; NaN/Infinity/exponents,
non-string financial values and negative timestamps fail. Trades use `trade_id`,
`taker_side`, `id_origin`, `timestamp_precision`, and genuine-source nullable
`timestamp_ns`. No ns value is synthesized from an ms/second source. Conflicting or
duplicate venue/category/symbol/trade_id keys in a provider page are rejected.
OI and funding endpoints enforce their own observation kind. A COMPLETE coverage
envelope cannot promote PARTIAL rows. Contract failures return sanitized HTTP 502.

Storage integration must use the coordinator's lossless adapter and B's authentic
provenance resolver. Without source/run context it remains unavailable. E performs
no C calculations. The desired factory protocol was published on Issue #5:
`factory() -> Reader`, `read(resource, Query) -> complete canonical envelope`.
Venue is bybit; opaque provider cursors must bind to resource and query filters.

The machine-readable contract is `contracts/v1/openapi.json`. Regenerate with
`python scripts/export_contract.py`; `--check` fails on drift. Breaking changes require
a new contracts major directory and explicit client coordination. No trading keys or
order endpoints exist. Bind to loopback only. No CORS write permission is granted.

Success: `{ "schema_version": "1.0", "data": ..., "next_cursor": null }`.
Errors: `{ "schema_version": "1.0", "error": { "code": "...", "message": "..." } }`.
Validation returns 422 without echoing secret-bearing input. Missing job: 404;
invalid transition: 409; unavailable provider/database: 503; quota/disk rejection: 507.
Health returns the Health envelope on both 200 and 503 (rather than the generic error).

Time ranges are UTC epoch milliseconds `[start_ms, end_ms)`. Monetary values and
quantities are JSON strings. `limit` defaults to 100 and cannot exceed 1000.
Opaque cursors are provider-owned, at most 512 characters, and must bind to the
resource and filters. Readers must return deterministic ordering, respect the range
and hard limit, and return a cursor only if another page exists.

| Endpoint | Data |
| --- | --- |
| GET /api/health | Health: database, worker heartbeat, disk, free/used bytes |
| GET /api/instruments | Instrument[] |
| GET /api/sources | Source[] with capabilities and availability |
| POST /api/downloads | Job (202) |
| GET /api/downloads/{id} | Job |
| POST /api/downloads/{id}/pause | Job |
| POST /api/downloads/{id}/resume | Job |
| POST /api/downloads/{id}/retry | Job |
| GET /api/coverage | Coverage[] |
| GET /api/candles | Candle[] |
| GET /api/trades | Trade[] |
| GET /api/footprint | Footprint[] (taker buy/sell, not inferred bid/ask) |
| GET /api/open-interest | OpenInterest[] |
| GET /api/funding | Funding[] |

Read query fields: category (`linear`, `inverse`, `spot`), symbol, start_ms,
end_ms, interval (Bybit-style string, default `60`), limit, cursor. Readers may
add resource-specific fields in a coordinated additive contract revision.

Download body example (one symbol/source/range per job):

```json
{"category":"linear","symbol":"BTCUSDT","source":"bybit","kind":"candles","interval":"60","start_ms":0,"end_ms":60000}
```

States: queued → running → completed/failed; queued/running → paused;
paused → queued via resume; failed → queued via retry. Progress is 0..1.
Pause invalidates the lease. Resume/retry preserves the last committed checkpoint.
Lease expiration allows another worker to recover; fencing prevents the previous
worker from publishing a checkpoint or database writes. A completed job means its
handler finished; it does not imply independently verified coverage.

## Adapter ports and cross-workstream coordination

`TESTER_PROVIDER_FACTORY=package.module:factory` creates a `Reader` with
`read(resource: str, query: Query) -> {data: list, next_cursor: str | None}`.
Response rows must match the resource model in `src/api/models.py`; every market
row has explicit provenance. Errors must distinguish absent capabilities from
an available source whose actual range is empty. Coverage complete requires B's
verification evidence; no inferred completion from record count.

`TESTER_HANDLER_FACTORY=package.module:factory` creates a handler with:

```python
run(request: DownloadRequest, checkpoint: dict, report, stop, commit) -> None
# report(checkpoint: dict, progress: float) renews lease; metadata only.
# stop: threading.Event; cooperative cancellation on pause, shutdown or lost lease.
# commit(write, checkpoint: dict, progress: float)
# write(conn) uses the supplied psycopg connection, NEVER commits/rolls back itself.
```

All durable provider writes MUST go through `commit`: it checks the lease while
holding the job row lock and writes storage data plus checkpoint in one transaction.
Stage external archive files immutably; publish their references inside that
transaction. Network calls must have bounded timeouts, check stop between chunks,
and must not be made while holding the commit transaction. Report or commit within
the lease period (default 60s). Callbacks can raise `LeaseLost`/`InterruptedError`;
handlers must propagate them. Exception text is never stored in jobs or logs.

This branch owns `tester_platform` only. B owns market schema/migrations and verified
coverage; A owns ingestion/catalog/source evidence; C owns derived calculations;
D owns frontend. Initial contracts were sent to all four chats. B confirmed UTC ms,
decimal strings, provenance and availability; its table/read API was still pending
design approval at the time of implementation. Provider ports deliberately return
503 and download jobs fail explicitly until these adapters are configured. This is
a platform acceptance, not a claim of end-to-end market-data acceptance.

The baseline E tests referenced `src.quality.candles` without including it. This
branch carries the unchanged initial B implementation from its branch to make the
existing suite runnable; B remains its owner.
