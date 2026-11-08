# K persistent isolated research vertical — 2026-11-08

This document supersedes the initial protocol-only state in K_CONTROL_CONTRACT_2026_11_08.md for the new source candidate. **Native application remains NOT_INTEGRATED.** This candidate is an explicit opt-in synthetic research path, never an installed ADMINIT replacement.

## Canonical PostgreSQL ownership and isolation

B/E research state lives in dedicated schema `tester_research`: immutable full F bundle/chart text and digests, persisted credential hashes/expiry/revocation and exact artifact grants, actor/request-digest jobs with fenced leases, immutable render acknowledgments and immutable audit. No SQLite authority exists. The actual G bridge, contracts, types and commands are vendored byte-for-byte at `5edb722fc936a56e81a174cfe654839de9a19a9d`; no G plane/PlanStore/API/main/runtime is imported or activated.

`migrate_research(dsn)` explicitly applies checksum-guarded SQL from `src/api/research_migrations`. It is never called by app construction, request processing or `src.api.db.migrate`, and no default/native settings are modified. DSNs require explicit loopback host, explicit non-5432 port and database name `tester_research_*`. Root's planned disposable port is 55471; no operation here was run against installed PostgreSQL.

For the isolated disposable experiment the migration/operator database role owns the schema. Runtime database credentials must be kept private to the composed process. This is an application authorization boundary, not a claim of production PostgreSQL role/RLS hardening. No existing data is migrated, reconciled or deleted.

## Concrete composition interface

```python
from src.integration.strategy_lab import run_certified_fixture
from src.api.research_ledger import (
    migrate_research, PersistentResearchLedger, create_persistent_control_app,
)
from src.api.research_g_client import ResearchGatewayClient

migrate_research(dsn)                 # explicit operator step on disposable DB only
store = PersistentResearchLedger(dsn)
bundle = run_certified_fixture()     # actual pinned F engine, SYNTHETIC_ONLY
chart_digest = store.publish_bundle(bundle) # invokes J verify_research_bundle, replaying F
# actor must be unique; token returned once, never stored in plaintext by this code
credential = store.issue_credential(actor, [chart_digest], ttl_seconds=600)
app = create_persistent_control_app(dsn)  # no DDL, default native factory untouched
# Root mounts static UI alongside the opt-in API and binds only loopback.
client = ResearchGatewayClient('http://127.0.0.1:18767', credential)
job = client.research_submit(command, actor=actor)
# root worker can poll store.pending_job_ids(actor, limit=16) and dispatch each
app.state.research_control.dispatch(store.authenticate('Bearer '+credential), job['job_id'])
result = client.research_result(job['job_id'], command)
# Actual D renderer sends its exact complete visible fill set after painting.
client.research_ack(job['job_id'], chart_digest, rendered_fill_ids)
```

`command` is strict `tester.research-command/1`: request_id canonical UUID string; action `chart.overlay`; run_id, snapshot_id, code revision, venue/category/symbol/interval, start_ms/end_ms/cutoff_ms and expected_artifact_digest. It contains no caller actor, arbitrary params, expressions or code. G first executes its pinned `validate_command` for the historical chart action, then sends the new strict typed request using actual pinned `GatewayClient._call` with loopback, disabled proxies/redirects, bounded JSON and redacted transport errors. No old lossy six-key overlay validator is reused.

Routes: POST `/api/control/research` -> 202 `{job_id,state}`; GET `/api/control/research/{job_id}` -> pending/running or `{job_id,state:'complete',artifact,artifact_digest}`; POST `/api/control/research/{job_id}/ack` with `{artifact_digest,rendered_fill_ids}`. Auth required on every request. Unauthorized token ->401; scoped object/action denial ->403; digest/replay/ACK conflicts ->409. Exact same-origin browser Origin is allowed, foreign/cross-site origins denied. Credentials stay in frontend memory only (D ownership).

Worker polling is bounded (1..64 IDs per call) and actor scoped. Claims use locked rows, fresh UUID fence and 30-second database-clock lease; expired running jobs can recover. Complete verifies current persisted credential/grant and scope within its transaction and fences stale workers. Pending/running queue limit is 256, serialized with a transaction advisory lock. Full result publication occurred once before the projection job; the job resolves approved immutable research, not caller-submitted strategy code. An optional one-job subprocess runner is `python -m src.api.research_worker JOB_UUID --actor ACTOR` using explicit `TEST_RESEARCH_DATABASE_URL`.

## Frozen chart/ACK semantics

Strict `tester.research-chart/2` is flat: binding fields above, execution_mode `SIMULATED`, coverage, source_ids, synthetic flag, required `candles` (bounded 2000 OHLC Decimal-string rows), and at most 500 required typed markers. Markers retain order/fill/position and nullable closed-trade IDs; price; timestamp, known-at and half-open uncertainty window; precision; required role `entry|exit` and execution side `buy|sell`. Actual F long/short entry/exit maps to execution side; originals remain in the full immutable bundle. Unknown/authentic provenance cannot be certified by rehashing: J's semantic verifier/replay owns admission, and this isolated publisher additionally rejects synthetic=false or non-SIMULATED bundles. Calendar-month interval M remains explicitly unsupported in this F path.

Canonical SHA-256 uses Python sorted compact JSON, `allow_nan=False`, default `ensure_ascii=True`, UTF-8 bytes. Artifact retrieval validates command scope plus both expected/stored digests. No marker timing or precision changes on persistence. A modeled-open fill is known at its window start; bar fills cannot be known before window end.

ACK locks the job and current persisted authorization, compares the completed stored digest and exact distinct visible fill IDs, and inserts receipt+audit in one transaction. Replay is idempotent. ACK is still a client's render report; root must separately inspect actual D rendering and record evidence. All markers must be known and within the chart's view.

## Tests and evidence boundary

Local source verification currently: **37 passed, 2 skipped** across research control and PostgreSQL suites. The skipped tests require explicit `TEST_RESEARCH_DATABASE_URL`; PostgreSQL correctness has not been claimed from this agent's environment. Tests are written to run J's genuine certified synthetic F bundle, publish/verify it, execute actual G HTTP against uvicorn+real PostgreSQL, expire a claimed worker lease, recover/complete in a separate Python process, read the same artifact in a second process, fence the stale lease, and enforce scoped replay/auth/ACK/audit. They do not silently replace PostgreSQL with an in-memory store. The old synthetic unit-store tests remain labelled as unit contract checks.

Root should compose J and L source commits before running:

```
python -m pytest tests/platform/test_research_control.py tests/platform/test_research_postgres.py -q
python -m ruff check src/api/research*.py tests/platform/test_research*.py
python -m ruff format --check src/api/research*.py tests/platform/test_research*.py
```

Missing actual J package/certified fixture is a failure once a PostgreSQL DSN is provided, not a fake successful provider. Optional `TEST_RESEARCH_BUNDLE_FILE` must still pass the same real J verifier/replay. No actual database pass, browser render pass, installed service update or production acceptance is asserted here. Root owns disposable PostgreSQL/HTTP/browser evidence and candidate composition. No UAC, installed service/config/data/ACL/registry, PG5432, office PCs, real orders or main merge is involved.
