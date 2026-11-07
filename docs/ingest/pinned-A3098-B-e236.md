# Immutable A3098 → B e236 PostgreSQL contract test

Requested by Issue #1 comment6709081686. Production code is read directly from:

- A `3098d55f2bb41c5389f83b955d87a3050ca52f85` (`src/ingest/`).
- B `e236395684d661f9464a62a0f6f67803d9000de6` (`src/storage/`, `src/quality/`).
- Shared1.0 `9766c0093e2608280875ac56db074414a3d62c69` (`contracts/`).

No A/B production code changes were needed for this pair. The new test command extracts immutable Git blobs into a fresh scratch snapshot, checks source SHA-256 values and imported module paths, and runs the real A parser/journal/export/storage projection against B's real migrations, `Store.ingest`, `Store.read`, provenance resolver and coverage ledger. The shared read projection is a **fixture projection**, validated using the pinned shared validator; this does not test or replace E's HTTP adapter.

## Reproduce

Requirements: Python3.11+, git, psycopg, jsonschema, and PostgreSQL binaries (`initdb`, `pg_ctl`, `postgres`). Fetch missing Git objects first; no foreign branch needs checkout or mutation.

```powershell
git fetch origin workstream/a-ingest workstream/b-integrity coordination/master
python -m unittest discover -v
python -m tests.ingest.pinned_roundtrip --work-root work/pinned-pair --pg-bin "C:/Program Files/PostgreSQL/18/bin"
```

Each invocation creates a unique directory containing a new loopback-only PostgreSQL cluster and six new fixture databases. It never connects to or migrates a user database. The child verifies the connected data_directory is the newly-created cluster before creating test databases. Logs and the source-hash manifest remain under the printed run directory. The server is stopped after testing; files remain for inspection. No directory, user database or source archive is deleted.

The parent stops and starts the same PostgreSQL cluster, then runs a new child process to read and replay persisted A exports. Cleanup checks the exact cluster's actual status, including unsuccessful start attempts, rather than trusting only an in-memory started flag. A separate real fault-injection check launched the disposable server and raised before that flag; the expected exit1 preserved the original error and cleanup stopped the server. This diagnostic can be repeated with `--simulate-start-error` (expected failure, not a passing contract test).

## Exercised assertions

| Case | Verified result |
|---|---|
| Equivalent Decimal lexemes | A export retains2 raw versions; B has1 economic trade and2 distinct source observations; strict shared read passes. |
| Original run declaration | Every accepted source has source_ingestion_run==caller run_id and source_run_status=matched. Wrong/missing caller and wrong/null/empty/overlong declared runs reject; six variants leave counts for **all** tester_storage tables and a prior checkpoint unchanged, including rejection after an earlier valid streamed row. |
| Original ns/ms/us | Exact source9-digit subsecond ns survives A→B→shared decimal-string projection; ms keeps null ns; us preserves timestamp_us with explicit unit conversion in the shared projection. |
| Genuine conflict | Changed economic price yields1 quarantine conflict,2 retained raw versions,0 publication batches and no B raw trades. |
| Pagination vs coverage | Actual producer fixture responses have counts2/1 and terminal EXHAUSTED; persisted coverage remains incomplete with0 verified percent. No terminal/cursor outcome promotes full history. |
| Replay and restart | SQLite reopen/export is lossless; B replay leaves whole-schema counts unchanged. Actual PostgreSQL stop/start plus a fresh process preserves1 trade/2 observations, matched original runs, and all schema counts after another replay. |

All inputs are explicitly **synthetic parser/API fixtures**. URLs model the production source format but are not evidence of source availability. No network market acquisition, live historical completeness, trader identity/history, TI source permission, E HTTP behavior or full A–G pipeline acceptance is claimed. This is a standalone tester contract check, with no main merge or physical deployment.

Timestamped results/source hashes: `pinned-pair-report.json`. Per-case output: `pinned-pair-tests.log`; actual restart: `pinned-pair-restart.json`; startup-failure cleanup: `pinned-pair-cleanup.json`. The existing bounded live evidence remains a separate earlier evidence class.
