# Runtime release acceptance

Baseline: `e64e30d8369626197403b3962f91bb350656e4c9`.

## Public transport correction

The baseline transport retried HTTP 403 and TLS failures and accepted arbitrary
paths on Bybit hosts. The Exchange Guardian halted these failures and accepted
only the trading archive root, excluding four existing archive consumers.

`src/ingest/public_access.py` is the shared pure request policy. It accepts only
the REST market endpoints consumed by the application (plus public orderbook
diagnostics), known public query names, archive discovery and the existing five
archive roots. Credentials, private paths, traversal, redirects and request
options that bypass TLS or add authentication are rejected before transmission.
Guardian identities omit validated market query values, so pagination shares the
same endpoint circuit.

The existing `Transport` remains the only ingest HTTP executor. A logical REST
operation shares a three-attempt default budget across HTTP and Bybit application
errors. Configured budgets are restricted to one through six attempts. HTTP 403
and TLS failures halt that endpoint in the transport instance until an explicitly
authorized manual reset; they never trigger automatic recovery actions. The halt
set has a 128-entry limit and refuses further work at capacity. It is in-memory;
the existing Guardian durable supervisor remains responsible for persisted policy.

429 retries require a valid Retry-After of one through 3600 seconds. Invalid or
oversized values fail closed. Timeouts and connection failures use bounded backoff.
Waiting respects cancellation at 250ms intervals; an in-flight HTTP operation is
bounded by its connect/read timeouts (defaults 15/60 seconds). Caller cancellation
does not forcibly interrupt requests while inside the HTTP library. A successful
later operation gets a fresh retry budget; safety halts require manual review.

Default sessions disable environment proxies and netrc credential inheritance.
Tests may inject a session; the embedding caller owns that session's configuration.
No private endpoint or trading operation is part of acceptance.

## Regression verification

From the repository, using its isolated environment:

```powershell
$env:GIT_CONFIG_COUNT = '0'
.venv/Scripts/python -m pytest tests/ingest tests/platform/test_exchange_guardian.py -q
.venv/Scripts/python -m ruff check src/ingest/transport.py src/ingest/public_access.py src/api/guardians/exchange_guardian.py tests/ingest/test_transport_release.py tests/platform/test_exchange_guardian.py
.venv/Scripts/python -m ruff format --check src/ingest/transport.py src/ingest/public_access.py src/api/guardians/exchange_guardian.py tests/ingest/test_transport_release.py tests/platform/test_exchange_guardian.py
```

The explicit Git variable clears an inherited incomplete GIT_CONFIG_COUNT injection
in the local tool environment, allowing existing immutable-source regression tests
to inspect their pinned objects. It does not change Git configuration files.

The Guardian cardinality regression now uses unique approved archive fixture paths
instead of fictional REST endpoints, preserving its 128-entry boundary assertion.

## Outstanding evidence

These transport regressions are offline fault tests. Public live Bybit reachability,
dedicated PostgreSQL integrity/load/restart measurements, whole-system browser flows
and Windows SCM lifecycle require separate evidence. Their status is NOT RUN at this
commit. A regional HTTP 403 must be reported as unavailable, never a live-data PASS.
No canonical installation, system PostgreSQL 5432, ADMINIT service or machine reboot
is used. `GUARDIANS_ENABLED=false` remains unchanged.
