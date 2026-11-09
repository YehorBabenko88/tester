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

Authenticated GET `/api/control/research-executions/{job_id}/summary` is limited
to that actor's completed execution and requires both current pair and artifact
ACL. It bounds the persisted bundle_text transfer to 8MiB, checks stored bundle
hash, genuine J/F replay and original execution strategy/snapshot/input binding,
then returns strict `SummaryEnvelope` from src/api/research_summary.py. No token,
grant, private DB field or caller-supplied financial value is exposed.

Envelope `{summary,summary_digest}` hashes canonical summary. Summary exact keys:
schema_version `tester.research-summary/1`, input_digest, artifact_digest, run_id,
source_sha, engine_version, certification, metrics, trades, equity_curve, brackets,
levels. Metrics/trades/equity/brackets are exact verified F facts, retaining
decimal/scientific strings, null uncertainty and confidence without calculations.
Limits: 500 trades, 2001 equity points, 2000 brackets, 1000 levels, 8MiB envelope
source bound. G's existing 1MiB transport cap stays unchanged and refuses larger
responses rather than truncating them; the browser applies its own bounded read.

Each level has exactly `{entry_fill_id,kind:'sl'|'tp',price,start_ms,end_ms,
known_at_ms}`: price copies recorded closed-trade SL/TP and times copy the
corresponding actual chart entry-fill uncertainty window. This is **entry-window
only**, never an assertion of full position lifetime or trailing updates. Exact
duplicate levels from partial exits deduplicate; conflicts reject. All trade
rows and brackets remain intact. Open brackets remain authentic table/point
facts; no open-position horizon or trailing line is invented. Chart/2 and its ACK
remain unchanged. Summary failure must leave the valid chart intact with summary
and levels unavailable. Standalone artifact reading without a completed execution
does not acquire summary permission implicitly.

Summary pure evidence: module-absent RED then eight tests PASS for exact facts,
bindings, original precision, tamper/rehash rejection, partial-exit presentation
dedup and typed G binding. Hosted PG/HTTP tests now include summary auth/hash/input
and chart binding but remain NOT_RUN until actual execution.

Executed locally: module-absent RED, then 10 pure tests PASS, four hosted-only PG
tests SKIP; Ruff check/format PASS. No local TCP/server/PG/browser/install actions.
Hosted workflow checks out exact snapshot-producer SHA separately, records both
source/tree/file manifests, and tests actual PG snapshot publication/read→F→J,
restart/replay, credential scope, quota, revocation, lost-lease fencing and real
G HTTP/Origin/ACK. Results remain NOT_RUN until that workflow actually executes.
Browser/product/native/real-history E2E is not demonstrated by these tests.
