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

## Actual transport observations and archive budget

Every actual GET attempt feeds the existing Exchange Guardian an Observation with
the validated, query-free endpoint, measured elapsed time, HTTP status or TLS /
connection failure, and a hash of sanitized request metadata. This hash is not a
response-body checksum or evidence of freshness. Guardian remains disabled by
default. Canonical supervisor events deliberately carry no ownership attestation;
an optional diagnostic sink can persist them through the existing supervisor, but
the transport never executes proposals. The existing configured API/worker JSON
logger records only closed, bounded fields, never query values or exception text.
Callback failure cannot clear a 403/TLS safety halt.
The catalog source now delegates its public GETs to the same Transport with one
attempt per scheduler-owned request, preserving its Windows system-trust adapter,
explicit CA option, body-size limit and public endpoint whitelist. Its transport
persists across pages/refreshes, so a 403 halt cannot disappear on the next page.
No environment proxy/netrc authentication is inherited. HTTP 429 retains the
existing `AcquisitionError("rate_limited")` contract for the catalog scheduler.
The former catalog system-trust adapter is now defined once in Transport and
reused by default REST/archive sessions. Live checking found that the catalog
worked with Windows ROOT trust while default REST failed certificate verification
on this host. Loading verified OS trust consistently fixes that mismatch without
disabling certificate or hostname validation or inheriting proxies/credentials.

Archive resumptions and HTTP retries now share the same logical operation budget.
URL validation occurs before destination creation. Cancellation during streaming
preserves partial evidence for a later explicit recovery. A regression first
observed nine HTTP attempts on offline archives and now requires three. Huge
integer clock values are rejected before floating-point conversion in Storage
and Exchange Guardians; endpoint-capacity pressure never evicts an existing halt.

## Dedicated PostgreSQL and performance evidence

`scripts/release_runtime_acceptance.py` extracts immutable Git archives and uses
the existing Store, JobQueue, worker `process_one`, application factory and PostgreSQL
client. It creates its own loopback cluster on reserved ports 55820–55827. API ports
are 55828–55829. It never connects to system PostgreSQL 5432. Clusters stop after a
run and remain as evidence; no production installation is adopted or altered.

The deterministic synthetic workload uses 300 exact-decimal OHLCV candles,
600 provenance observations, duplicate replay, interrupted transaction rollback,
conflict rejection, four concurrent replays and 40 real queued worker transactions.
Thirty real localhost HTTP requests validate health response bodies. A pending
job/checkpoint and candle digest must survive PostgreSQL immediate stop and crash
recovery; the old lease token must be fenced. Existing storage, queue and research
PostgreSQL tests then execute against the disposable cluster, with skips rejected.

Three repeated pairs on Windows 11 / CPython 3.12.10 / PostgreSQL 18.1 compared
baseline `e64e30d8369626197403b3962f91bb350656e4c9` with candidate
`b81c01faef9de5c52490b7cf3c23c996b6ba1df5`. All six revisions/runs passed 34
existing PostgreSQL tests plus workload and crash-recovery assertions. Median
candidate/baseline ratios were:

| Measurement | Median ratio | Three-pair range |
| --- | ---: | ---: |
| API mean latency | 0.9736 | 0.9451–1.0264 |
| API p95 latency | 1.0580 | 1.0297–1.2656 |
| Worker jobs/second | 0.9666 | 0.9629–1.0050 |
| Candle rows/second | 1.0080 | 0.9830–1.0120 |
| Sampled aggregate peak RSS | 0.9846 | 0.9623–1.0073 |
| Sampled process CPU seconds | 1.0110 | 0.9974–1.1222 |
| PostgreSQL WAL bytes | 0.99996 | 0.99699–1.00290 |

These are short runs, not a performance GO. Process I/O counters are not physical
disk traffic. Separately reported system disk counters include unrelated host
activity. Aggregate RSS can count shared mappings more than once, and 50ms process
sampling misses short-lived backend activity. These limitations apply equally to
both revisions and are not claims of complete CPU/RAM/disk accounting.

Evidence resides under `work/release-runtime-repeat-final/run-20261123T233708-e778716b`,
`run-20261123T233822-0dfb3e64`, and `run-20261123T233933-68ff1e2e` in that work root.
Each run has manifest/source pins, raw results, PostgreSQL logs, existing-test logs,
crash recovery evidence, cleanup status and public response samples with SHA-256.

A separate candidate-only compatibility run used staged production CPython 3.14.10
and PostgreSQL 18.7 with the same pinned candidate. All 34 existing PostgreSQL tests,
workload checks, crash recovery and public probes passed. This is ABI compatibility
evidence, not a performance comparison across interpreter/database versions.
Its evidence root is `work/release-production-compatibility/run-20261123T234246-4cce4d49`.

Replay using the isolated development environment:

```powershell
.venv/Scripts/python scripts/release_runtime_acceptance.py --baseline-repo <baseline-repository> --candidate-repo <candidate-repository> --candidate-ref <exact-SHA> --pg-port 55821 --api-port 55829 --soak-seconds 600 --public-probes --ca-bundle <trusted-CA-bundle>
```

The optional soak repeats real queue transactions, history reads and HTTP health
validation for the requested duration. It records progress every 30 seconds,
throughput, queue depth and latency; requests are bounded and all final jobs must
survive crash recovery. Use `--candidate-only --pg-bin <production-PG-bin>` with the
production interpreter for a separate compatibility check. No 24-hour acceptance
has been performed.

## Public exchange access and remaining boundaries

Public read-only Bybit time, three fixed historical BTCUSDT one-minute candles for
2024-01-01 and a bounded 32KiB archive-directory sample returned HTTP 200 with
qualified bodies in all three repeated runs and the production compatibility run.
Only three historical candles were requested; historical coverage remains UNKNOWN.
The archive probe proves directory access, not complete archive download. The CA
bundle was explicitly trusted; TLS verification and hostname checks remained on.
A regional HTTP 403 must be reported as unavailable, never a live-data PASS.
Whole-system browser flows and Windows SCM lifecycle require separate evidence.
No canonical installation, system PostgreSQL 5432, ADMINIT service or machine reboot
is used. `GUARDIANS_ENABLED=false` remains unchanged.

## Optional native persisted research projection

`Settings.research_projection_enabled` defaults to false. Enabling it requires
an explicit `research_database_url` for the existing isolated loopback,
non-5432 `tester_research_*` database contract. The native release profile can
pass the installer-prepared research App-role DSN. Startup never migrates,
publishes a fixture, issues a capability or enables a qualified producer.

The existing persistent control application is composed by `main.create_app`
using the existing ASGI composition, retaining child authentication, object ACL,
origin guards and exact artifact binding before the static frontend catch-all.
Without a capability the research HTTP endpoints return 401; cross-origin
requests return 403. Research database failure returns a fixed, secret-free 503.

The existing worker checks market work first and delegates at most one eligible
projection to the existing fenced `PersistentControl.dispatch` when idle.
There is no additional worker, engine or retry executor. Selection uses persisted
principals with current credentials and grants. Claims are durably limited to
three attempts. Malformed pre-claim work consumes a compare-and-update failure
budget without double-charging an acquired lease. Expired/revoked principals and
exhausted jobs are not automatically retried. Exhaustion requires an explicit
operator decision; it never resets itself across worker restarts.

Operators must separately publish a verified artifact and issue its expiring,
revocable capability through the existing ledger operator API. Tokens belong in
the research page's in-memory capability field, never a URL or diagnostic log.
The existing frontend and contract retain `SIMULATED` / `SYNTHETIC ONLY` labels.
Real-market strategy execution remains **UNAVAILABLE**: the existing producer and
execution-input gates only admit certified synthetic inputs. Route wiring does
not qualify real market data or promote a test fixture into production evidence.

New regression tests cover default-off configuration, strict DSN validation,
startup without DB/producer/credential side effects, native route security,
safe unavailable responses, market priority, durable attempt exhaustion,
malformed stored work, revocation/expiry and concurrent lease fencing.
The original fourth-claim behavior fails the new attempt regression.

Physical acceptance used CPython 3.14.10 and PostgreSQL 18.7 with SCRAM and the
actual nonowner App roles. Twenty-seven affected tests passed. The existing
Playwright research-backend test passed against the native application factory,
reference-prices/catalog profile and actual `src.api.worker` process, with no
manual ASGI composition. The operator explicitly supplied a labelled synthetic
test artifact and capability. A research-only DB outage produced HTTP 503 while
the same worker handled an explicitly rejected test market job; API reconstruction
preserved the completed projection and ACK. Graceful worker stop and disposable
cluster stop were observed. This is process/API acceptance, not Windows SCM or
real-market backtest certification.

Precommit evidence with exact changed-file hashes is in
`work/native-research-projection/run-20261124T002824-eb7d0e68`; it records the base
SHA and tested file content separately. Replay the committed revision before
claiming final-published-HEAD acceptance.
