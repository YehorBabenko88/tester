# Local control API v1.1 — handoff to E

`ControlAPI.handle()` returns `(HTTP status, JSON value)` and never binds a socket.
E supplies a `TokenAuth`, one `ControlPlane`, fixed policy limits, a build commit
SHA (`code_revision`), an exact allowed browser origin and a random CSRF secret.
Identities come from an external credential store, never a request body. Provision
independent expiring action/scope grants per agent. Tokens are SHA-256 indexed in
memory, compared in constant time, revocable, and never saved in the control DB.

## HTTP boundary owned by E

- Bind explicit loopback addresses only; validate peer and Host to resist DNS
  rebinding. No permissive CORS, redirects, proxy forwarding or remote bind.
- Accept `Authorization: Bearer ...`; cap raw body at 32 KiB before JSON decoding,
  reject duplicate JSON keys/NaN, bound request time and concurrent connections.
- Forward the actual Origin to `handle(origin=...)`; browser mutations require an
  exact allowed Origin and `csrf=...`. Nonbrowser calls still need bearer auth.
- `local_human_channel=True` is a trusted E session attribute, never a header or
  JSON field. Approval also requires an authenticated human Identity and CSRF.
  Never provision a human credential to Codex or expose approval via MCP.
- Apply ingress/read/error rate limits, concurrent strategy-gate admission and
  OS CPU/memory limits outside the kernel. Kernel quota is global/per-actor active
  jobs and persisted new plans per minute, checked before expensive planning work.
- Catch storage/internal exceptions as generic 503; never return traceback, token,
  source text or request body. Record unauthenticated failures in E's bounded,
  redacted transport audit; authenticated rejections are recorded by the dispatcher.

## Routes

| Method/path | Input | Output |
| --- | --- | --- |
| GET `/api/control/capabilities` | none | effective action/scope grants and policy limits |
| POST `/api/control/plan` | complete v1.1 envelope | immutable canonical command, intent hash, preview, expiry, approval requirement, plan_id |
| POST `/api/control/jobs` | `{ "plan_id": "UUID" }` | 202 job; exact retries return same job |
| GET `/api/control/jobs/{id}` | own job UUID | command, plan, state, progress, result, approval actor, intent hash |
| POST `/api/control/jobs/{id}/cancel` | `{}` | own job, idempotent terminal state |
| GET `/api/control/jobs/{id}/events` | `after=0&limit=100` | ordered lifecycle/progress events with global monotonic seq |
| GET `/api/control/jobs/{id}/artifacts` | none | bounded immutable artifact manifests |
| GET `/api/control/audit` | `after=0&limit=100` | actor/scope-filtered append-only records and hash-chain links |
| POST `/api/control/approvals/{plan_id}` | `{}` + trusted human session/CSRF | approved exact plan; never caller-provided approval boolean |

Status: 400 invalid envelope/parameters, 401 unauthenticated, 403 capability/Origin/
CSRF/human denial, 404 unknown route/resource, 409 intent collision/stale revision/
expiry/evidence conflict, 429 quota. Errors contain only stable `error` codes.
Pages allow 1..500 records; `after` is nonnegative integer. Use the final returned
seq for the next page. Empty events page is a poll result, not job completion.

## Command envelope

All keys below are required, including nullable keys. Unknown fields are rejected.
Times are integer epoch seconds for request lifetimes and integer UTC epoch
milliseconds for historical ranges. UUIDs must be canonical lowercase strings.

```json
{
  "schema_version": "1.1",
  "request_id": "c0b83c9f-0725-4bc0-a8bb-7520721ed00a",
  "actor": "agent:g",
  "scope": "lab",
  "correlation_id": "536f4306-7a0b-4553-92fd-b4b9a6c10308",
  "issued_at": 1794051000,
  "expires_at": 1794051600,
  "action": "history.coverage",
  "category": "linear",
  "symbol": "BTCUSDT",
  "start_ms": 1700000000000,
  "end_ms": 1700000060000,
  "snapshot_id": "market-1",
  "params": {}
}
```

Example lifetime is illustrative; clients must use their current UTC time. Lifetime
is at most 900 s, no future issued_at accepted. Plans also expire with their
creating identity. Historical windows are half-open, at most 366 days and cannot
extend beyond current UTC. Market actions require category/symbol/range/snapshot.
System actions and instrument.search may use null market fields.

## Typed parameters

| Action | Parameters (required unless marked optional) |
| --- | --- |
| instrument.search | optional query (`[A-Z0-9_-]`, <=40 chars), limit |
| history.coverage | optional limit, cursor |
| history.download | interval, data_kind: trade/mark/index/premium |
| chart.navigate | interval |
| chart.overlay | run_id, revision, interval; optional limit, cursor |
| analytics.run | analysis_id (server registry ID), interval |
| strategy.validate | strategy_id (stored DSL ID), strategy_revision (40-hex commit) |
| backtest.run | strategy_id, strategy_revision, validation_id |
| backtest.compare | 2..10 distinct run_ids |
| system.health | empty params |
| system.config.preview | optional expected_revision, changes; changes requires expected_revision |
| system.config.apply | expected_revision, nonempty changes |
| dev.change.request | base_revision (40-hex), branch (`codex/[a-z0-9_-]+`), task_ref (`issue:N`) |

Identifiers are restricted ASCII, <=100 chars; revision is integer >=0. Intervals:
1,3,5,15,30,60,120,240,360,720,D,W,M. Resource settings are max_workers/max_downloads
1..8 and memory_mb 128..16384 only. No paths, URLs, free-form code, shell, SQL or
service-control operations. `types.py` supplies static types; `contracts.py` is
the runtime schema authority. No automatic migration from the legacy v1.0 command
format; clients must add explicit actor/scope/lifetime/correlation/snapshot fields.

## Plans, approval, configuration and replay

Deduplication key is `(authenticated actor, request_id)`, across all scopes.
Canonical SHA-256 covers every envelope field. Same key/different intent is 409;
identical plans/jobs persist across restarts. Expired request envelopes cannot be
replanned; retrieve existing job by ID. Retain dedup tombstones if archival is added.

Approval binds to persisted plan/hash/scope, expires after 300 s or plan expiry,
and is consumed by the plan's single job. Configuration and development changes
and simulations require human approval. Submit rechecks current grants, plan expiry,
approval and trusted simulation evidence. Caller cannot edit the approved plan.

Configuration is a versioned **desired resource policy**, not a Windows process
operation. Preview records before/after; apply changes the journal atomically using
expected_revision. Conflicting approved plans have one winner. Rollback is a new
preview/approval/apply containing the previous values at the current revision.
E must reconcile this journal to bounded workers and report effective settings;
G does not claim the Windows service has applied them. Internal config jobs finish
in the same transaction and retain preview/provenance/result hash. Missing build
SHA is recorded as null, never fabricated; E must supply its build SHA in release.

## Worker protocol — service-only, never public routes

`claim(worker, lease_seconds=60)` returns a job, canonical command and opaque lease.
E dispatches only to a fixed registry of typed trusted providers. No default shell,
SQL or arbitrary import. Enforce actor/scope/snapshot authorization on every source,
strategy, run and artifact reference. Core actor isolation does not replace domain
object authorization. Recheck current identity revocation before dispatch.

`progress(job_id, lease, 0..99, lease_seconds=60)` renews lease; progress cannot
decrease. Equal-value heartbeats do not add logs. Leases are <=300 s.
`complete(job_id, lease, result)` validates bounded result/provenance/overlay before
commit. `fail(job_id, lease, stable_code)` accepts provider_unavailable,
invalid_evidence, provider_failed, resource_limit, never raw exception messages.

States: queued -> running -> succeeded/failed; queued/running -> cancelled;
expired queued jobs and expired running leases -> interrupted. `recover()` is safe
across concurrent processes and never requeues interrupted work. Starting another
process does not steal a live lease. Late completion/progress is fenced by lease,
state and expiry. E must stop/checkpoint cooperatively and enforce OS cancellation;
DB fencing cannot undo external effects. Do not automatically retry side effects.

All metadata transitions and audit/events use SQLite transactions with durable
sync and unique constraints. Kernel uses FIFO scheduling and explicit active-job
caps. Hash-chain audit is tamper-evident only if E anchors chain heads externally;
an OS user who can rewrite the database can also rewrite the chain.
