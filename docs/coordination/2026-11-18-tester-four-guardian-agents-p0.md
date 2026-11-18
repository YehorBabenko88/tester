# Tester Guardians P0 — four autonomous, fail-closed controllers

Status: DRAFT architecture and deterministic decision engine only. NOT deployed or wired to services.
Parent integration: Draft PR #83. Scope: Tester Preview on ADMINIT only, simulated/read-only market data.
No live orders, no exchange trading keys, no access to PostgreSQL 5432, no SSH lateral privileges.

## Source evaluation

https://github.com/ashishpatel26/500-AI-Agents-Projects is an index of examples and external links, not a certified production guardian fleet. Reusable *ideas*:
- `agents/02-code-review-agent` — LLM diagnoses code, but cannot write running services.
- `agents/15-unit-test-generator` — generate regression tests only into isolated worktrees.
- `agents/07-github-issue-triager` — classify errors to issues/Draft PRs.
- `agents/04-sql-query-agent` — read-only database interrogation pattern; sample targets SQLite, not PostgreSQL.
- Avoid importing `agents/08-data-analysis-agent` into the runtime: its example deliberately permits model-generated Python execution with `--allow-dangerous-code`.
Licensing and dependencies of independently linked projects must be audited individually; external README entries are not a single vetted library.

## Topology

One Windows SCM service or scheduled supervisor owned by Tester; four separately permissioned workers, each with bounded heartbeats and budgets. Reliable core **does not require an LLM, Codex, API token, browser, or Internet except Bybit for re-download**. Optional LLM only reads redacted evidence and may draft a GitHub PR with new tests. No arbitrary command execution and no direct edits to running files.

Event: `{component,signal,evidence_ref,timestamp,source,repeat_count,ownership,scope}`. Journal is append-only; dedupe by evidence hash + scope; each decision includes prior state, reason, action, lease id, idempotency key and after-check. Never send PostgreSQL credentials, TLS bundles, private paths or raw data records to a model.

### 1. Code Guardian

- Signals: API / worker stopped, stale heartbeat, bounded error-rate, CI regression.
- Deterministic: validate ownership, 3 independent failed probes, one owned-service restart per incident, <=2/hour, verify health and correct version; circuit-breaker on repeated failure.
- Unknown defect: sanitize logs; open GitHub issue and optionally isolated worktree/Draft PR with test-first repair. Require Ruff/pytest/integration + exact-head CI + manual gate before deployment. No self-modification of active code.
- Dependency: existing `src/api/worker.py` heartbeats and `src/api/jobs.py` leases.

### 2. Data Guardian

- Read-only monitoring: checksum/provenance, OHLCV sanity, closed-candle cadence, storage coverage, unique record identity, catalog status, queue checkpoint consistency and WAL/disk health.
- Missing verified candles: enqueue bounded, idempotent `bybit` backfill through the *existing* CatalogHandler + fenced transaction; after-check must re-read raw record and original projection, compare source hash.
- Conflicting or unverifiable data: quarantine affected range, emit evidence and alert. Never synthesize candle prices, promote coverage, update raw rows in place, repair by LLM-written SQL, or delete source evidence.
- Independent baseline and sampled reconciliation; isolate problems per `category/symbol/interval/window`.

### 3. Storage Guardian

- Observe free bytes, own runtime quota, job journals, PostgreSQL growth, disk/backup health. On low disk pause new downloads, preserve running job safety.
- Auto-clean only named `*.part` in explicitly owned `runtime/tmp` when >=24 h old, and only using the existing `cleanup_temp` function (no symlink/junction/reparse traversal).
- Historical candles, raw metadata, audit, retained releases, DB backups, WAL are not a default cleanup target; time/size retention for each is a separate explicit policy requiring verified backup + dry-run manifest + ownership check.
- No auto-VACUUM FULL, DROP, TRUNCATE, DELETE or external drive sweep.

### 4. Exchange Guardian

- Monitor public Bybit HTTPS request success, connection/latency, 429 and `Retry-After`, source freshness, response parsing and retry budgets; check REST and WebSocket separately only when enabled.
- Use existing `src/ingest/transport.py` with host allowlist, TLS validation, request throttling, bounded exponential backoff, no redirects; own state machine prevents retry storms.
- 429: obey Retry-After and source rate budget. TLS failure: **halt requests and alert**, never disable certificate checking. Prolonged outage: mark `UNAVAILABLE`, leave existing data immutable, resume via measured gap reconciliation only when endpoint healthy.
- No IP/proxy rotation to evade exchange controls.

## Supervisory invariants

1. Only one writer per scope (lease + epoch fencing), all effects idempotent and journaled.
2. Observe -> classify -> policy gate -> bounded action -> verify -> record receipt -> alert/escalate.
3. Monitoring and diagnostics never assert recovery without after-check, and never suppress `DATA_UNAVAILABLE`.
4. Every actor has least-privilege credentials and a separate allowlist. The Code Guardian has **no DB write rights**. The Data Guardian writes only via the existing queue/storage APIs; model cannot submit SQL. Storage Guardian owns only approved temp space. Exchange Guardian has outbound HTTPS read-only permissions.
5. Kill switch + manual pause + safe shutdown. Respect API worker leases and do not restart an unrelated executable.
6. External projects are examples, not dependencies, unless versions, licenses, and security are reviewed.

## Acceptance sequence

- P0a: pure deterministic policy unit tests, unknown inputs fail closed, zero side effects (this PR).
- P0b: read-only probes, persistent alerts and receipts under isolated Preview; no worker changes.
- P0c: controlled restart and backfill in disposable database with injected failures and exact-key reread.
- P0d: owned-only temp cleanup and 429/TLS fault tests; simulate outage and reconnect.
- P0e: optional offline/self-hosted LLM diagnosis and Draft PR workflow, never automatic merge/release.
- P0f: Windows SCM restart/stop, unattended reboot, 24h soak, leak check, on/off toggles and isolation check against 5432 before enablement.

Release criteria: 0 unauthorized writes, 0 false data-verification claims, all deterministic fault injections pass, independent post-repair evidence, exact-head CI and physical ADMINIT acceptance. Until then GUARDIANS_ENABLED=false.
