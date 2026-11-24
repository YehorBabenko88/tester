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
and PostgreSQL 18.7 with the same pinned candidate. All 34 selected acceptance tests with PostgreSQL enabled,
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

The existing worker processes market work first and then delegates at most one
eligible projection to the existing fenced `PersistentControl.dispatch`, checking
shutdown again before delegation. This bound permits both queues to progress
under a continuous market backlog without another worker or executor.
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

The same native factory/worker/browser acceptance was repeated after commit
`2df28d9d2f070d7e47d89c005cbac50aca5e6beb`: 27 tests and the Playwright test
passed, including research-DB outage isolation, persistence after API reconstruction
and graceful worker/cluster shutdown. Evidence is
`work/native-research-projection/run-20261124T003102-a66c947c`.
The main strategy-execution panel remains unavailable: only the separately
authenticated persisted-projection viewer is wired, not execution/summary routes.

Review identified starvation under a continuous market backlog. Commit
`ca1b69084f5a1356d9576099a71a3e451800062d` bounds delegation to market-first,
then at most one projection with a stop recheck. The new ten-cycle regression
first observed zero projection progress and now requires ten market-first
projection delegations. The actual PostgreSQL/route suite passed 29 tests, and
the existing Playwright case passed from the terminal's approved-results link
against this committed frontend/backend revision. Evidence:
`work/native-research-projection/run-20261124T004328-c6b090d0`.

## Completed bounded soak and live history roundtrip

An immutable same-interpreter comparison pinned baseline
`e64e30d8369626197403b3962f91bb350656e4c9` and candidate
`cc74009a89889b7753832b236cf6cc21a5aa6f53` on CPython 3.12.10 / PostgreSQL 18.1.
Both completed the requested 600-second synthetic queue/read/HTTP workload and
passed the 34-test selected suite with PostgreSQL enabled, crash recovery, row digest/checkpoint
preservation and old-lease fencing. Both clusters were stopped.

| Sustained observation | Baseline | Candidate |
| --- | ---: | ---: |
| Measured seconds | 600.520 | 600.166 |
| Soak jobs completed | 3260 | 3176 |
| Real HTTP health/body checks | 815 | 794 |
| Soak jobs/second | 5.4286 | 5.2919 |
| Soak API mean / p95 ms | 43.855 / 137.186 | 46.612 / 142.239 |
| Sampled CPU seconds, whole workload | 205.484 | 206.125 |
| Sampled aggregate peak RSS bytes | 361353216 | 356474880 |
| PostgreSQL WAL bytes | 13662624 | 13247040 |
| Database bytes | 13129407 | 13088447 |

Both queues peaked at four and returned to zero after every batch. The sustained
candidate throughput was 2.52% lower and API mean latency 6.29% higher; API p95 was
3.68% higher. The initial 40-job burst was 17.55% lower, while the three earlier
short-pair median was 3.34% lower. Keep this distribution and measurement context;
do not infer a 24-hour/SLA/performance GO. Total completed work differs during a
fixed-duration throughput run, so raw WAL/DB/I/O totals are not equal-work ratios.
OS-wide disk deltas include unrelated host activity; sampled process counters and
aggregate RSS have the limitations described above. This common runtime workload
does not test native SCM or the later opt-in projection/host-restart-budget profile.
Evidence: `work/release-runtime-soak600/run-20261123T235018-462f2e2c`.

Candidate-only CPython 3.14.10 / PostgreSQL 18.7 compatibility was repeated at
published `64591cc91f5cf0f9c36d0dd81e4c838afd94ca28`: 34 selected tests with PostgreSQL enabled,
runtime/HTTP checks, crash recovery and public probes passed. This is separate
from the same-interpreter performance comparison and predates projection wiring.
Evidence: `work/release-production-compatibility-latest/run-20261124T001326-359b97eb`.

A real public-history roundtrip at
`2dfb5d5e1904aa94c39936a0b2426ef84d0d6f54` used production CPython 3.14.10,
PostgreSQL 18.7 and default verified OS trust. Existing Backfill acquired three
BTCUSDT one-minute candles for UTC `[1704067200000,1704067380000)`, verified two
original HTTP-body hashes, exported a Journal handoff, passed the shared record
contract, and published/replayed through Store with declared-run provenance.
All three records, checkpoint and read digest survived a PostgreSQL immediate
crash/restart. This proves the bounded window only; global coverage is UNKNOWN.
Evidence: `work/real-history-production/run-20261123T235832-89d01c56`.

## Durable native public-network policy

Canonical native configuration pairs `public_transport_policy_enabled=true` with
the installer UUID in `public_transport_installation_id` and the existing main
application DSN. The policy adapter uses the existing `api.db.connect` boundary;
there is no second PostgreSQL client, transport, supervisor or retry executor.
Its DSN must explicitly require `scram-sha-256` authentication, pin loopback and a
dedicated non-5432 database. Pure CLI transports without this configuration remain
transient and are not evidence of native durable acceptance.

Owner migration `004_transport_policy.sql` creates the installation scope and
bounded request state. The installer owner seeds the scope, revokes all App-role
table access, and grants only read/reserve/finish functions. API catalog and worker
history use that same scope. Each actual GET reserves a durable PENDING token
before transmission. HTTP 403 and TLS failures latch HALTED; streamed responses
retain their reservation until context close, including TLS failure during body
iteration. Successful responses or explicit ordinary close clear only their own
PENDING token. App callers cannot clear HALTED state, delete rows or substitute a
boolean for database-owner authorization.

No state is expired or evicted automatically. At 128 records further network
admission fails closed. Unknown scopes, malformed state, missing schema, failed
authentication or unavailable PostgreSQL deny network access. Normal concurrent
requests to one endpoint wait within the sole Transport's bounded admission
window. A late security outcome after its token was revoked globally halts the
scope rather than forgetting the fault.

Only the installer database owner may call
`tester_platform.reset_public_transport(installation_uuid, endpoint_key)` after
review. A NULL endpoint explicitly reviews/resets the whole scope, including a
global halt. No public HTTP reset route exists. Corrupt schema/state requires
owner repair. A process crash or failed state write can leave PENDING even if no
403/TLS was observed; this deliberately requires manual review rather than
guessing that retry is safe. This availability tradeoff must not be represented
as unconditional automatic network recovery.

Canonical native history now uses the existing verified OS-trust adapter with
environment credentials/proxies disabled before the existing HttpMeter wrapper.
It does not depend on legacy `state/tls.json`. The opt-in legacy lab path retains
its pinned bundle/hash checks. Certificate and hostname verification remain on.
The hard network policy is independent of disabled Guardian effectors;
`GUARDIANS_ENABLED=false` is unchanged.


Committed native policy/readiness replay at
`8b41d277e9a40b7f7eaaf87fa0fb36e9c38b14ca` used CPython 3.14.10 and PostgreSQL
18.7 on disposable ports 55824/55828 with SCRAM mutual authentication. The actual
release initializer seeded the shared transport authority: no manual policy
seed/grant or configuration override was used. All 47 selected acceptance tests
passed (19 PostgreSQL-backed cases and 28 cases without PostgreSQL); the
authenticated native API/worker/browser flow passed (4.3 seconds).
An actual CatalogHandler download completed three public BTCUSDT minute candles
with authentic declared provenance through the existing fenced queue and Store,
without a legacy TLS manifest. This is a bounded window, not historical coverage.

Migration 005 records the existing service instance in worker heartbeats. Native
health accepts only a fresh heartbeat matching the API's captured instance; a
fresh previous-instance heartbeat cannot make a restarted API ready. The real
PostgreSQL regression covers previous/current instances and legacy standalone
NULL semantics. Evidence is in
`native-research-projection/run-20261124T011701-08381ad2` within the sanitized
runtime evidence bundle. API/worker and disposable PostgreSQL were stopped after
the replay; no SCM install or 24-hour soak is claimed.

## Public response ownership and cooperative cancellation

The sole Transport admits a durable reservation before allocating the actual
send slot. Concurrent sends share its dispatch gate; valid Retry-After cooldown
is recorded even when the current operation has exhausted its attempts. Real
Requests sessions, including the native HttpMeter's wrapped session, must have
verified TLS, no inherited environment authentication/proxies, no credentials,
cookies, hidden query parameters, response hooks or hidden adapter retries.
Opaque injected request protocols remain a trusted integration/test seam, not
a sandbox for arbitrary Python code.

Every response retains admission until its owned cleanup completes. A normal
non-streaming response caches its body and closes its network resources before
being returned; JSON and content remain readable from that cache. Streaming
responses retain PENDING through active readers and closers. TLS failures in
body reads, iterator finalizers and response close cannot release the reservation
as an ordinary success; an unavailable policy database leaves PENDING.

REST API and Backfill propagate cancellation into Transport. Waits check it at
most every 250 ms; stream readers check before/after each completed chunk and
Backfill checks before publishing a newly returned result. A socket read already
in progress is cooperative: the configured connect/read-inactivity timeouts are
capped at 15/60 seconds. This is not an unconditional wall-clock cancellation
deadline for a slow-delivery peer. Once cancellation is observed, no later GET
or body chunk is published. An inactive read failing after cancellation is
reported as a pause; TLS faults still retain their security outcome. Native
service stop additionally owns the existing process JobObject lifecycle.

Acceptance counters are suite totals unless explicitly classified. For example,
the later 94-case runtime suite contains 28 PostgreSQL-backed cases and 66 cases
without PostgreSQL. The paired 703 performance selection contains 29
PostgreSQL-backed cases plus five pure cases per revision. Exact node IDs and
source hashes accompany the sanitized runtime evidence; direct HTTP/worker and
browser flows are reported separately from pytest counts.
