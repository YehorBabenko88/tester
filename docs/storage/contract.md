# Storage contract 1.0 (approved)

PostgreSQL schema `tester_storage`. Python package `src.storage`. Configure `TESTER_STORAGE_DSN` or libpq environment/service; do not commit credentials. `migrate(conn)` is an explicitly privileged setup operation; normal Store does not run DDL.

Envelope: `schema_version="1.0"`, `venue="bybit"`, `category`, `symbol`, `dataset` (trades, candles, mark, index, premium, open_interest, funding, ratio), `timestamp_ms` (integer UTC epoch ms), `interval` for periodic series, `payload` (decimal strings), `source` (url, fetched_at UTC ISO8601, optional sha256). Trade payload: trade_id, price, size, side (Buy/Sell), optional timestamp_ns and timestamp_precision (ms/us/ns); timestamp_ns is populated only for genuine ns input. Original source payload is preserved. Missing trade_id requires source record_key and a documented composite identity, tagged composite; such records cannot independently prove completeness.

Evidence is a separate immutable request/page ledger: request parameters, cursor, next cursor, requested [start_ms,end_ms), count, status EXHAUSTED/EMPTY/PARTIAL/FAILED/UNVERIFIED. EXHAUSTED means pagination finished, not complete market history. Verification additionally requires explicit available-range evidence and dataset checks. No empty result implies COMPLETE.

Implemented in B; user authorization covers implementation, coordination with A/C/E/MASTER, Issue #2 and Draft PR; never merge. See operations.md for verification criteria, credentials, migrations, recovery and limitations.

## Stable Python interfaces

```python
from src.storage.migrations import migrate
from src.storage.store import Store

# Explicit setup under owner/admin; migrations never run during Store construction.
with owner_connection as conn:
    migrate(conn)

store = Store()  # TESTER_STORAGE_DSN or libpq service/environment
result = store.ingest(records, evidence, job_key='public-backfill:window-id', checkpoint={'cursor': None})
rows = store.read('trades', 'bybit', 'linear', 'BTCUSDT', start_ms, end_ms,
                  interval='', limit=1000, after=None)
# rows sorted by (timestamp_ms,record_key), all numeric prices/volumes are strings.
# Next call uses after=[rows[-1]['timestamp_ms'],rows[-1]['record_key']].
coverage = store.coverage('bybit','linear','BTCUSDT','trades','',start_ms,end_ms)
```

Trade interval must be empty. Time precision supports `s`, `ms`, `us`, `ns`; seconds require whole-second epoch-ms, microseconds use exact timestamp_us, ns requires exact timestamp_ns; the latter is NULL otherwise. Original envelope is preserved separately from canonical numeric payload, so formatting changes such as 123.00 vs 1.23E+2 do not cause semantic conflicts.

Evidence scope fields match records exactly. Example (not verified; no invented availability):

```json
{"venue":"bybit","category":"linear","symbol":"BTCUSDT","dataset":"trades","interval":"","start_ms":1700000000000,"end_ms":1700000060000,"status":"EXHAUSTED","pages":[{"params":{"start":1700000000000,"end":1700000059999},"cursor":null,"next_cursor":null,"count":1}],"available_range":null}
```

REST collectors using timestamp-bound pagination rather than exchange cursors should record a synthetic **request continuation token** in cursor/next_cursor that links each subsequent page's actual request boundary. This token is acquisition evidence, never a fabricated exchange cursor; put pagination mechanism in params. A single window/page can use null cursor and null next_cursor. Source request evidence must be preserved exactly; never label a saturated page EXHAUSTED.

## Read-side integration for E/C

`tester_storage.instruments`: venue/category/symbol primary key. `instrument_metadata`: effective_ms, revision, metadata, provenance. Consumers must select the latest applicable effective_ms with a documented revision policy; no arbitrary metadata merge.

`raw_records` is the single source of truth. SQL read views `raw_trades`, `raw_ohlcv`, `price_series`, `open_interest`, `funding` share its columns. Prices/quantities are PostgreSQL numeric, payload/provenance JSONB. `record_sources` links record_key to every source plus raw_envelope. `source_archives` contains indexed manifests. `pagination_evidence` records request/page/validator results. Prefer `Store.coverage` to ad-hoc coverage counts because it applies active findings, resolutions and archive-byte verification.

`derived_aggregates` stores algorithm_version, source_fingerprint and payload containing `{data,scope}`. `save_aggregate(evidence,payload,algorithm_version)` stores a revision; `check_aggregate(id)` verifies trade-derived volume/count/OHLC and reports stale source fingerprints. This is a verification contract for C's aggregates; it is not a footprint calculation engine. Equal-time trades sort by actual sub-ms precision then original ID; true exchange sequence within equal source timestamps cannot be inferred.

E's HTTP envelope `{schema_version:"1.0",data:...,next_cursor:null}` remains its API responsibility. Storage records are JSON-safe; do not cast decimal strings to binary floats in shared models. HTTP unavailable/unknown status should reflect the coverage result. E owns its separate `tester_platform` queue; B's download_jobs/checkpoints track atomic publication and repair, not HTTP queue ownership.
