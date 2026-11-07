# Codex Control Plane — implementation architecture

## Boundaries
Codex is an optional orchestration client, not a privileged resident process with direct PostgreSQL/Windows authority. The Windows service owns a local authenticated gateway; Codex Desktop, an MCP-compatible client, or the terminal may submit typed commands using an approved adapter. Codex integration support must be verified before implementation; no undocumented endpoint is assumed.

## Command lifecycle
1. Client submits intent; `/api/control/plan` parses to canonical typed plan with assumptions, required sources, estimated work and permissions.
2. Read-only requests can be scheduled under scoped capabilities. Backtests need a validated declarative strategy and pinned market snapshot.
3. Destructive/runtime configuration or code-change requests require preview and explicit human approval. Source changes are separate Git worktrees/PRs, not live patching.
4. API records immutable actor/request ID, intent hash, approval ID, scope, job ID, evidence references, outputs and outcome; duplicate IDs with different hashes reject.
5. Durable worker runs bounded jobs and emits progress/events; D polls/streams bounded overlays by run ID, not generated HTML.
6. Cancellation/OS shutdown checkpoints jobs; restart cannot replay approved mutations without new authorization.

## Security
Loopback-only gateway, origin/CSRF checks for browser, separate short-lived agent tokens scoped by action and expiry, least-privilege OS service, secrets stored outside Git, per-action rate limits and quotas, audit redaction. Explicit policy for Codex-controlled code changes: isolated branch, tests, review, never auto-merge or hotpatch service. Prompt injection in source data must never become an instruction or tool action.

## Overlay data
`{schema_version:"1.0",run_id,revision,venue,category,symbol,interval,start_ms,end_ms,availability,coverage,provenance,markers:[{timestamp_ms,kind,price,trade_id,fill_id,reason}],levels:[{start_ms,end_ms,price,kind,rule_id}],next_cursor}`. Markers require a persisted fill or explicitly tagged signal; the UI must distinguish them. All decimal amounts are JSON strings; UTC half-open ranges. No bid/ask inference from trades.

## Owned modules
G owns src/control/, tests/control/, docs/control/; E owns HTTP gateway/auth/service and worker adapters, F strategy and backtest job engine, D overlays/command UI, B provenance/coverage storage, MASTER integration and acceptance.

## Current state
Only initial typed command validator and unit-test source exist on this branch. No authenticated HTTP gateway, MCP server, agent connection, installed Windows service, persistent job store or end-to-end tests have been implemented or run yet.
