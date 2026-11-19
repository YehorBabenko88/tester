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

## ADMINIT desktop launcher / full-uninstall Exchange acceptance (contract 13d26b74)

MASTER alone implements both desktop shortcuts and the installer/uninstaller.
Exchange Guardian supplies offline tests and acceptance requirements only.

- **Launch:** `GUARDIANS_ENABLED=false` verified before any startup.
  No exchange API keys, cookies, tokens, credentials or private account
  endpoints in launcher arguments, console output, logs, manifests or receipts.
  Offline localhost health is not evidence of Bybit reachability or candle
  coverage. No live trading, background HTTP retry, TLS bypass or rate-limit
  evasion. Failure to verify the dedicated installation/ownership must block
  startup, never silently fall back to system PostgreSQL on 5432.
- **Uninstall:** the launcher must not leave a Guardian background process,
  scheduled task, retry timer or outbound HTTP request after confirmed stop.
  All owned Exchange configuration, diagnostics, history and test results are
  in the explicitly attested TESTER deletion scope. The uninstaller owned by
  MASTER requires exact typed `DELETE TESTER` plus second destructive
  confirmation, including loss of downloaded candle history and no backup.
  Never delete unrelated files, PostgreSQL 18/5432 or foreign service data.
- **Fault injection:** test offline launch, missing/invalid manifest, stale
  TLS evidence, 429 Retry-After, interrupted shutdown, tampered service path,
  malformed URL, repeated uninstall and reparse-point denial using disposable
  fixtures only. Proposals remain disabled by default. Do not run uninstall
  or tests against active ADMINIT services.
- **Evidence:** pin exact package SHA and ownership manifest, record
  redacted result per test and verify zero leaked credentials and zero
  exchange/trading requests. A restored HTTP 200 never proves history
  completeness; separate Data Guardian reconciliation is mandatory.

These criteria do not implement deletion or override the MASTER contract.

## Canonical-main consolidation / integration risk review

- **No duplicate transport:** `src/ingest/transport.py` already owns
  actual official Bybit GET, session, throttling and its retry loop
  (default attempts=6). Exchange Guardian is strictly **diagnostic and
  proposal-only**, not a second HTTP client. MASTER must prevent additive
  retries (6 transport attempts multiplied by 3 Guardian attempts);
  only one bounded network retry budget may be executed per operation.
- **Policy separation:** `src/api/guardian_policy.py` performs pure
  authorization; this module provides per-endpoint observed state. No
  market-data backfill, history completeness promotion, trading order
  or data mutation is within Exchange ownership.
- **Supervisor compatibility:** The child's historical branch may not
  contain `src/api/guardian_supervisor.py` even when the newer
  foundation PR does. Standalone Exchange policy tests run without it.
  Optional integration test explicitly SKIPs when that module is absent
  (and must PASS on the validated combined candidate). Never vendor
  the MASTER-owned supervisor into this workstream to make a test pass.
- **Ownership evidence:** `supervisor_event(..., ownership_verified=True)`
  must only be supplied after an independent caller verifies the
  endpoint/scope and evidence digest. The default is fail-closed and
  cannot authorize action. Endpoint memory is capped at 128 scopes
  to prevent unbounded growth; encoded/ambiguous paths fail closed.
- **Redundant or historical files to review, not delete here:**
  `docs/guardians/workstream-exchange-p0.md` is the original scaffold
  alongside this canonical acceptance document. Historical
  `scripts/windows_preview/` and `scripts/windows/` installers are
  unrelated to the new `C:\\TesterLab` target. MASTER #88 and PR #94
  must inventory references and deployment pins before any deprecation.
- **Release gate:** The final merge candidate must verify real
  exact-HEAD Python/Ruff/fault-injection and combined transport +
  supervisor coverage, plus Windows safe-shutdown and independent
  archive-gap reconciliation. An HTTP 200 is never a coverage receipt.

All of the above are integration findings, not authorization to
merge, install, uninstall, use real trading APIs or alter main.

## Connection fault-injection scope (2026-11-19)

The decision engine now recognizes `dns_failure`, `websocket_disconnect`,
`partial_response` and HTTP 403 as externally supplied observations.
DNS and WebSocket disconnect share the existing bounded transient retry
proposal budget (maximum two proposals before circuit opens). HTTP 403
halts and requires manual review: it must never trigger proxy rotation,
credential swapping, IP evasion, or automatic retry. Partial responses
open a protective circuit and never authorize candle writes. TLS
verification failure always halts, including during another cooldown.

**WebSocket boundary:** `endpoint_key` accepts only attested official HTTPS
source identities, not arbitrary `wss://` URLs. A separately authorized
WebSocket transport must supply observations tied to an approved source
identity; this Guardian does not open or reconnect sockets. No websocket
handshake, ping/pong, subscription, order or trading code is implemented.
MASTER must review transport ownership and a unified rate-limit budget.

**Data boundary:** connection recovery means only transport monitoring;
historical completeness, candle deduplication, gap reconciliation and
writes belong to Data Guardian. The Exchange module does not access
PostgreSQL or historical storage.

Fault-injection tests are deterministic and offline. The new cases are
not physical network fault-injection evidence until executed on an exact
pinned HEAD. `GUARDIANS_ENABLED=false` remains the default.

## HTTP 403 precedence and foundation refresh gate

HTTP 403 now preempts an active 429 or timeout cooldown, transitions the
endpoint to HALTED, and emits no retry proposal. Repeated 403 observations
remain halted. No credential rotation, proxy/IP evasion or live trading is
allowed. Offline negative tests cover 403 during both cooldown types.

MASTER foundation candidate `5813d6471b50a9d79bc6eb6b2adf6fdbc79588a7`
includes shared policy test formatting; branch synchronization must wait
for its exact-head Python, Platform and isolated fault CI to pass and
MASTER to approve a non-force refresh. Do not copy or independently edit
`tests/platform/test_guardian_policy.py`. The new unified installer is
MASTER-owned and `GUARDIANS_ENABLED=false` remains mandatory.
