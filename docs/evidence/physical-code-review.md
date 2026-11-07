# Physical SCM code-only review evidence

Parent d28f492c6584f38cafa30d6345db2d34710b7565; separate workstream/e-physical-scm,
Draft against workstream/e-platform, no merge. Issue27 authorizes MASTER alone as
physical operator. E performed NO SCM/service/RunAs/ACL/firewall/PG modification.

2026-11-07 local Windows/Python3.14: `python scripts/acceptance.py --security`
PASS163 tests,14 skipped (12 opt-in DB cases +2 symlink privilege),1 upstream
Starlette TestClient warning. Lint/format, pinned shared/policy bytes, OpenAPI and
financial schema drift, Bandit and pip-audit PASS. Native routines were mocked;
20 new tests cover manifest/owner/foreign service fences, bootstrap budget, recovery
flags/counter, per-instance readiness, immutable endpoint config, job assignment
before resume and honest forced shutdown failure. Physical/postgres integration
acceptance intentionally not run in this E stage. See physical-code-gate.log.

Independent static review identified 4P2 issues before publication: missing
non-crash recovery flag, unauthenticated instance readiness, uncounted early
bootstrap errors, ResetPeriod0 resetting SCM counter. Corrected with regressions.
Direct pywin32 C-source review also showed normal SvcRun return overwrites prior
STOPPED failure as success; errors now propagate to native dispatcher. Forced
shutdown timeout is recorded/reported as failure, not graceful success.

Payload/operator contract: docs/physical-scm-payload.md. Root marker agreed with
MASTER before publication. Fixed root/service/API/PGscope; LocalService; no remove
or cleanup; two SCM restarts thenNONE plus persisted3budget; no ledger reset tool.
No production derived reader/tick default activation. Prior d28 financial changes
are retained. Actual DLL/privatevenv/service protocol/ACL/DB/stop/recovery and
physical acceptance belong to MASTER and remain unproven by these mocks.
