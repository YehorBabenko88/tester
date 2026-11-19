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

## CI regression record (2026-11-18)

Prior exact HEAD `7e0a5230093f3df7587af5e9cfc0f2a8867e11f9` failed
Python compileall due to a malformed `pytest.mark.parametrize` decorator in
`tests/platform/test_exchange_guardian.py`. Platform acceptance also reported
Exchange-owned Ruff I001 and UP017. Both are corrected in the Draft PR.
Foundation `tests/platform/test_guardian_policy.py` I001 is MASTER-owned and
is deliberately not changed here. New exact-head CI must independently verify
these corrections; queued jobs are not a PASS. Never infer complete history
from HTTP 200 or a restored transport circuit.

## Monotonic-clock fault injection

A backward caller monotonic timestamp now halts the endpoint instead of
shortening a protective 429 cooldown. The halt requires explicit manual reset;
it does not authorize a network retry or claim historical completeness.
This is an in-memory proposal engine only, not durable restart protection.
The new regression cases exercise rollback and repeated identical timestamps.

## Offline reinstall preflight (NO EXECUTION)

1. Capture existing Tester service names, service identities, executable paths,
   startup types and status without stopping services.
2. Record existing Preview release SHA, config file hashes, enabled flags,
   dedicated database name/port and storage paths; explicitly exclude 5432.
3. Inventory existing Bybit public source URLs using sanitized host/path only.
   Never export credentials, API keys, cookies, authorization headers or tokens.
4. Confirm Guardian disabled in both environment and configuration. Do not
   start an HTTP probe, download history, connect a trading account or infer
   complete history from 200.
5. Require independently verified backup manifests, a recoverable dedicated
   database snapshot, file hashes and a restore drill in a disposable location.
   Existing data and active services remain untouched.
6. Pin candidate exact commit and signed release artifacts. MASTER must approve
   compatibility, installer contract and rollback before any installation.

Rollback contract (planning only): preserve prior binaries/config, immutable
data backups and a versioned release manifest. A later approved installer must
be able to restore previous binaries/config without downgrading or overwriting
data, and must independently verify dedicated database identity and record
integrity. No uninstall, stop, service mutation, PG5432 access or deployment
is authorized by this document.

Acceptance remains NO_GO until exact-head Python, Ruff, fault-injection,
independent backups, Windows ADMINIT and MASTER integration checks pass.
