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
HTTP cursors are E-authenticated tokens (at most 4096 characters), bound to the
resource and normalized data filters, including native source cadence and page limit.
An observation's display viewport does not change its source cursor scope. Provider-owned source cursors
are decoded before `Reader.read` and wrapped on return (at most 2048 source characters
and within the transport byte budget). Readers must return deterministic ordering, respect the range
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

Read query fields: venue (`bybit` only), category (`linear`, `inverse`, `spot`), symbol, start_ms,
end_ms, interval (bar interval or observation display viewport; default `60`),
source_interval (native observation cadence), candle_kind (default
`official_trade`; also official_mark/official_index/official_premium/derived), limit, cursor. Readers may
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

Schema validation alone is insufficient. E now checks each market row against the
original request, never the mutable query copy passed to the provider: venue/category,
symbol when supplied, timestamp in `[start_ms,end_ms)`, requested resource kind,
native observation/candle interval, candle_kind and bounded bar windows. Footprint v1 has no
interval field; its declared UTC window must have the requested interval duration.
Fixed minute bars start on their exact Unix UTC grid; daily bars start at UTC midnight,
weekly bars start Monday00:00 UTC. Calendar-month validation checks successive UTC
month boundaries using integer timedelta from the epoch, never floating timestamps.
Floats/bools are rejected as source timestamps/boundaries even when numerically integral.
This computes no market aggregate. Query ranges must contain returned full bar windows.
Align a bar request accordingly.

Observation source cadence is independent of chart display timeframe. Funding always
resolves to `event`; explicit source_interval other than event fails422 before provider
invocation. OI accepts explicit source_interval in5min/15min/30min/1h/4h/1d, default5min.
The display interval never implicitly chooses OI cadence, even when it looks like1h.
Examples: funding with `interval=D&source_interval=event`; OI with
`interval=60&source_interval=1h`. There is no E/browser resampling or C calculation.
For bars, a source_interval override that differs from interval is rejected rather
than silently resampling. The additive input is documented in OpenAPI.

Reader receives a private Query with interval=effective native cadence,
source_interval=the same cadence, viewport_interval=original display interval. Scope
checking and cursor signatures use an independent normalized query that is never
passed to the provider. For observations, viewport changes60→D do not invalidate a
cursor if native source cadence stays the same; changing native cadence does.

The envelope coverage range must exactly equal the requested range, including empty
pages. Row coverage must lie within it and contain the row timestamp. Coverage-only
rows are checked for contained bounds. Shared v1 coverage/source-capability records
do not encode a market identity; their identity authenticity remains the scoped
storage resolver's responsibility. E cannot infer evidence ownership from bare bounds.
No shared v1.0 schema file was changed.

Wrong provider scope returns502 provider_scope_mismatch. Error context retains
upstream availability status and a bounded provenance summary (kind/content hash and
digests of source/run/reason), never raw payloads, source URLs, DSNs or credential text.
The provider response is copied before checking and serializing it. Public continuation
tokens use an app-local HMAC key, so tampering/cross-resource/data-filter reuse is rejected
with422 cursor_scope_mismatch BEFORE provider invocation. A process restart or another
API instance invalidates these page tokens; restart pagination from the first page.
This does not affect persisted job checkpoints. Provider source cursors remain lossless
behind the wrapper, and providers should retain their own scoped cursor validation.

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
