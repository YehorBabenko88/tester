# Exchange Guardian — P0 offline acceptance

Owner: Issue #87 / Draft PR #93. Scope: public Bybit REST only.

## Safety and boundaries

The module is a pure decision engine. It does **not** call the exchange, restart
services, access PostgreSQL, submit orders, update candles, or run shell/LLM code.
`GUARDIANS_ENABLED=false` remains the deployment default; instantiate only
with `enabled=True` in isolated tests. Proposals require a separate independent
supervisor policy and manual deployment gate. No automatic retry is executed.

Allowed URL identities are exact official HTTPS hosts and `/v5/market/` paths,\nplus `/trading/` archives on `public.bybit.com` only,
without query strings, credentials, fragments, explicit ports or redirects.\nThis is deliberately narrower than Transport's host-only allowlist;\nGuardian does not authorize arbitrary endpoints merely because Transport accepts them.
Each endpoint has a separate in-memory circuit. TLS faults preempt cooldown. TLS failure is a persistent halt
until explicit manual reset. Invalid Retry-After triggers a protective one-hour
pause rather than an immediate retry. Timeouts and HTTP 5xx yield at most two
bounded retry proposals before opening a circuit. All times are monotonic caller
timestamps; evidence digests must be generated independently.

A successful HTTP 200 with verified freshness means only transport monitoring is
healthy. Both `history_complete` and `gaps_reconciled` are always false.
Data Guardian must independently reread historical ranges and verify original
records before claiming coverage. No synthetic candles or data mutation.

## Windows ADMINIT read-only smoke

Run from an isolated checkout of this PR, not the live Preview worktree:

```powershell
$env:GUARDIANS_ENABLED = 'false'
py -3.12 -m pytest tests/platform/test_exchange_guardian.py -q
py -3.12 -m compileall -q src/api/guardians/exchange_guardian.py
git rev-parse HEAD
git status --short
```

No Bybit credentials or live network required. Do not modify Windows services,
PostgreSQL port 5432, active Preview or trading configuration.

## Evidence and remaining acceptance

Tests cover disabled mode, allowlist/SSRF, 429 and Retry-After (including HTTP dates), timeout exhaustion,
TLS fail-closed and manual reset, stale source, HTTP 500, malformed/redirect,
duplicate evidence, per-endpoint isolation, and recovery without history claims.

Local Windows physical fault injection, exact-HEAD CI and 24-hour soak are
independent release gates: until proven, **NO-GO**. Module is not deployed.

Source patterns reviewed: KorroAi/mue-x orchestrator roles, error fingerprints,
memory lattice, guard and sandbox. Only typed roles and verified evidence concepts
were adopted; no autonomous code execution, mutable learning or subprocess sandbox.
