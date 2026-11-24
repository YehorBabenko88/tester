# Normative architecture — tester only

User direction and Issue25 supersede any earlier interoperability proposal. YehorBabenko88/tester is a standalone system. It has no runtime, import, repository-path, shared-database, module-registry or Gateway dependency on Unified, Grid, Strattester, Math, MetaAnalyse or Terminan. Do not inspect or modify those repositories to implement tester.

Allowed architecture is tester's own A ingestion → B PostgreSQL/provenance → C calculations → E local API/service → D terminal, with F offline simulation and G typed local Codex control. Legitimate declared third-party libraries and permitted market-data providers are dependencies of tester, not other user-project services.

The existing src/integration/storage_api.py is an internal B→E boundary, not an inter-repository adapter. Current G GatewayClient is a loopback client for tester E's /api/control; its name alone does not imply a prohibited foreign Gateway. Do not remove correct internal transport just because a symbol says gateway. No foreign provider/client/registry was found in the reviewed published runtime files; retain exact review scopes rather than claim exhaustive security certification.

src/integration/standalone_policy.py adds an ownership-only provider check: only tester component modules with source under the owned checkout, no URL/path/expression/global Python fallback, and exact loaded-origin verification. E/G must call it before import and use a trusted owned import path; loaded-origin check precedes callable invocation. It performs no imports/execution itself. This helper is not a Python sandbox and does not authorize importing arbitrary prompt-generated code; capability/approval gates still apply. It is preparatory until E/G adopt it in their own reviewed changes.

Canonical permanent market/trader evidence, research and authoritative job/audit state belongs in tester's PostgreSQL through reviewed B/E contracts. SQLite is staging/cache only. G's current durable SQLite plans/jobs/config/audit require a documented canonical-store decision and migration/adapter; don't create a second authoritative trader ledger. No change/deletion of existing user data is authorized.

## Product sequencing

P0: integrate and independently verify current A–G, exact SHAs, common contracts, scoped reader/authentic provenance resolver, quality/cursor semantics, control authorization, kill/restart, bounded resources, ten-instrument test and actual CI. Keep main unchanged.

S0: source availability/access/terms research and inert source registry for Trader Intelligence; this may run now because it enables no acquisition and claims no data permission. S1+ product ingestion/storage/analytics/overlays follow P0 and separate reviewed contracts/Draft PRs. See docs/trader-intelligence/INTEGRATION_PLAN.md. Anonymous executions never gain a trader identity; reconstruction never becomes observed fact.
