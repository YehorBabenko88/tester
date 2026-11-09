# Issue59 F/J/G/K execution seam — operator scoped, SYNTHETIC only

Base ea4d9b2cbfc15e1af54ea6bf73a530b68763873e. Snapshot producer dependency:
PR61 ffa312659507699d132f9f111a7708428feb86be. Financial authority remains
the existing pinned F engine; no additional price/PnL/risk calculations.

Explicit setup: operator runs `migrate_research(dsn)`; B repository `.migrate()`
and `.publish_fixture()` are separate explicit calls. Neither factory migrates,
starts a worker, downloads data, installs or touches native services. Root owns
the reviewed worker lifespan and activation; `pending_executions(actor)` and
`dispatch_execution(actor, job_id)` are the durable worker interface. This PR
does not automatically enable the new routes in the ordinary product startup.

Operator registers a bounded validated DSL via
`ExecutionLedger.register(strategy, snapshot_repository, snapshot_id)`. The
repository resolves its stored immutable qualified snapshot and verifies its
producer evidence; the HTTP caller cannot supply snapshot bytes, SQL, DSL, code
or actor. The execution input digest binds strategy + full qualified snapshot
and provenance metadata. REAL, non-admitted, UNKNOWN or changed snapshot hash
is rejected. Current J publication is SYNTHETIC_ONLY / NOT_PROVEN, even if a
future real snapshot admission changes: this code deliberately has no real path.

`issue_execution_credential(actor, input_digest, ttl_seconds=600)` creates a
separate server-side pair grant and returns a random credential once, storing
only its hash. An existing chart-overlay credential cannot execute. Runtime
checks current expiry/revocation and pair/job ownership on submit/read/claim/
completion; it never accepts caller identity. Active admission is capped at
four jobs per principal. Explicit operator issuance is not a public endpoint.

Opt-in `create_execution_app(dsn)` preserves the existing Host/Origin middleware
and authenticated chart/ACK routes. `compose_execution(market, execution)` mounts
both prefixes before main static routing and composes lifespans. HTTP contract:

* POST `/api/control/research-executions`: `{schema_version:
  "tester.research-execution/1", request_id:<canonical UUID>,input_digest:<64hex>}`.
* GET `/api/control/research-executions/{job_id}`: `{job_id,state}` while pending
  or running; failed adds `reason:"ATTEMPT_LIMIT"`; complete adds `chart_command`
  (existing research-command/1) and `certification:{scope:"SYNTHETIC_ONLY",
  real_market_coverage:"NOT_PROVEN"}`. Terminal submit replay may return this
  same terminal result. UI captures selected scope and validates command binding.
* 401 unauthenticated, 403 scope/Origin, 409 replay/contract, 422 invalid typed
  request, 429 active quota, 503 infrastructure unavailable.

Same actor/request replays the same durable job; a different input conflicts.
One database lease admits actual F execution. Completion requires the current
unexpired lease token and current auth/grant. It commits the result command,
one artifact-read grant for that result and audit atomically. J publication is
independently immutable/idempotent: a crash may leave an ungranted artifact,
which is replayed on retry; it cannot create a completed job or broaden access.
Expired lease recovery permits at most three claims; the fourth attempt records
ATTEMPT_LIMIT. Failure details never become caller-provided executable code.
Direct operator DB mutation remains outside the runtime authorization model.

Chart rendering keeps the existing command/digest and exact rendered-fill ACK
contract; this execution result is not an ACK and is not a renderer substitute.
Existing chart Command supports twelve intervals, excludes M. No new statistics,
SL/TP or journal values are invented; existing F bundle is canonical evidence.

Executed locally: module-absent RED, then 10 pure tests PASS, four hosted-only PG
tests SKIP; Ruff check/format PASS. No local TCP/server/PG/browser/install actions.
Hosted workflow checks out exact snapshot-producer SHA separately, records both
source/tree/file manifests, and tests actual PG snapshot publication/read→F→J,
restart/replay, credential scope, quota, revocation, lost-lease fencing and real
G HTTP/Origin/ACK. Results remain NOT_RUN until that workflow actually executes.
Browser/product/native/real-history E2E is not demonstrated by these tests.
