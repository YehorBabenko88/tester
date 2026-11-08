# Windows Research Lab additive opt-in integration — review plan

**DOC_ONLY / NOT_IMPLEMENTED / NOT_AUTHORIZED_FOR_NATIVE_ACTIVATION.** Root is the sole physical operator. This plan grants no UAC, service/ACL/registry change, installed read, worker recovery, PostgreSQL 5432 operation, deployment or automatic scheduling authority.

## Source baseline and intended behavior

The final verified source baseline is **A43**, the head of [PR #39](https://github.com/YehorBabenko88/tester/pull/39) when this plan was prepared. Earlier `68e59b5` / `8a0e894` source and CI failures are historical context, not the current qualification state. This document updates the published J plan from `f8f49447cff2dfc713cfddc03dd473506c31b29e`; it changes no implementation.

| Frozen evidence | Exact identity |
| --- | --- |
| SourceCodeSHA / PR #39 source | `a43d155a382e93f1c4ad09c55a4c6265eea00242` |
| Source tree | `372a310062fcaee95c93ecfc5c12103eeb3b7600` |
| Published source archive SHA-256 | `a8c6b63075a752b589eedf9be827a1501917a8da0f67edc0ca8505fc84900c0a` |
| Retained native application source | `719b02eea4134730b5d688143022706a9cae8dbe` |

A plan-only commit is not a new application SourceCodeSHA. A43 and PR #39 remain unchanged by this plan. Every future implementation candidate and native action must freeze its own exact reviewed source/tree/archive and runtime/configuration identities.

## Final A43 qualification and remaining NO_GO boundary

The coordinator reports the following completed evidence for the exact A43 source; this plan does not rerun physical operations or inspect private lab material:

- All four recorded CI runs are green. Their exact-source results are [frontend contract/browser](https://github.com/YehorBabenko88/tester/actions/runs/42522411989), [platform acceptance](https://github.com/YehorBabenko88/tester/actions/runs/42522412028), and the two recorded [Python static/unit](https://github.com/YehorBabenko88/tester/actions/runs/42522412270) [runs](https://github.com/YehorBabenko88/tester/actions/runs/42522407309). The platform matrix includes Linux 3.11, Linux 3.14 and Windows. The coordinator inspected raw checkout logs for all 10 jobs and confirmed actual checkout SHA A43, without merge-ref ambiguity. The platform Windows run passed 708 tests / 65 subtests with 79 environment-gated skips, including actual non-admin, individual-owner and read-only DACL write-denial evidence. Operational launcher health remained honestly degraded with provider unavailable, and graceful stop passed.
- Actual research G HTTP ownership/replay checks passed **20/20**, preserving the same job/digest on replay. The maximum observed wait was 30 observation polls / 1,488 ms before the strict accepted `ESTABLISHED` row appeared. Initial `NONE` / `CLOSE` observations were never accepted. The owned PID, process-birth and exact TCP four-tuple predicate remained mandatory. This is an observed successful sample, not proof of the earlier refusal's cause or a concurrency/saturation result.
- **33** physical PostgreSQL/auth/F-coverage tests passed, and one real-backend Playwright test passed against the separately owned research composition, including zoom, drag, keyboard interaction and the three-fill ACK. Final durable evidence contained one artifact, three completed jobs and three ACKs, with one attempt per job. Synthetic F qualification and refusal of unauthentic coverage do not establish authentic market coverage.
- The coordinator normally idle-ended research API PID 6688. Independent canonical research database evidence remained unchanged at SHA-256 `29b3b1d0fa416f609326aafe7dfafede9feb82a35fbd9ee891748aaa7b878725`. The separately owned research PostgreSQL PID 22216 was then normally stopped and its data retained.
- Retained native 719 identity and observed app PID 6860 / owned PostgreSQL PID 5132 were unchanged; foreign PostgreSQL 5432 PID 11740 remained unchanged and outside the change. All three SCM service PIDs and the native identity remained unchanged; no research listeners remained after the normal research shutdown. The coordinator's context remained non-admin. These are recorded observations, not reusable PID authority for any later operation.

These passes qualify the bounded portable research candidate, **not an installed Windows service addon**. Authentic historical coverage, least-privilege Research roles, real LocalService secret/source/database integration, resource quotas/performance, native service-account physical tests, backup/restore and compatible app-only rollback remain **NO_GO / NOT_PROVEN**. No native activation, UAC, recovery or native upgrade is authorized.

The portable `src/integration/research_lab.py` and `research_runtime.py` composition is a separately created owner-user cluster at 55471 and research-only HTTP app at 18767. It uses a user-only private directory, bootstrap owner and one-hour demo capability. It reports `native_service=false`. These properties are not a native service launch/configuration/authentication contract. LocalService cannot be assumed able to read the current owner-only `private.json`.

The primary proposed integration is **additive inside the existing main application and its owned app service**. Preserve all existing market/download APIs, canonical market storage, operational worker, chart behavior, identity and health contracts. Do not replace `src.api.main` with `research_runtime.create_app`, its root static mount or its research-only worker. The present native host launches the existing supervisor; a future addon requires explicit source composition, not a registry module-string substitution.

## Typed default-off boundary

Define and review versioned typed configuration with `research_enabled=false` by default, an independent secret Research DSN and explicit owned database/role identity. Keep the market DSN separate. Add reviewed worker/resource bounds and expected research source/schema versions. Reject unknown fields, unsupported combinations and enabled-without-provisioned-identity rather than selecting defaults.

With the feature disabled, startup must preserve the existing main application behavior, make no research database connection or migration and start no research polling task. Existing unavailable UI states must stay honest. Research-enabled startup validates the pre-provisioned owned database and schema, checks current credentials/source pins, then mounts only the reviewed bounded research routes/assets. Failure must be visible in versioned health and cannot be represented as successful research activation.

Preserve the existing owned main API endpoint (currently 18766) and all existing paths. Mount reviewed research namespaces without shadowing `/api/downloads`, market/chart routes, main identity/health or the frontend root. Fix the standalone control factory's hard-coded origin/port assumptions through typed reviewed composition; do not widen Host/Origin/CORS to arbitrary origins. Any genuinely necessary sidecar/listener is a separate design decision and approval, never an automatic port fallback.

## Database, migration and roles

Use a new owned `tester_research_<installation-id>` database with its own DSN and least-privilege runtime role. Its PostgreSQL endpoint and cluster system identifier must be explicitly selected and approved. If the existing owned lab cluster on 55470 is selected, use a distinct Research database/role and reviewed backup scope; never use its market `tester_lab` database as an implicit fallback. If a distinct research cluster is selected, review its ownership and lifecycle separately. Never connect to, migrate, stop or adopt PostgreSQL 5432.

The portable fixed 55471/18767 marker is not a substitute for this native ownership decision. Reject occupied ports, foreign cluster identity, missing database/role, ambient libpq redirects and unsupported DSN fields. Preserve established ownership checks and exact endpoint authentication.

Bootstrap/provisioning and checksummed migration must occur as explicit reviewed operator actions **before application startup**. Do not execute DDL from client requests, service startup or app factory construction. Separate the migration owner from the runtime role; the portable initdb `research_owner` bootstrap account is not production least privilege. Review only necessary table/sequence permissions and immutable artifact/audit/ACK triggers. Test denied DDL, cross-database/schema access and forbidden evidence mutation.

Canonical Research PostgreSQL remains the sole authority for credentials/grants, jobs, fencing/leases, immutable bundle/artifact bytes, ACKs and audit. No parallel SQLite/state-file success ledger. A separately approved seed/import path must run the actual pinned F verifier and publish canonical bytes; an HTTP command cannot request arbitrary engine code, shell, SQL or provider expressions.

## Account, secret ACL and source protection

The running identity remains a reviewed least-privilege app service account, such as the existing scoped LocalService contract. Use the exact owned service name/installation identity rather than generic process/service matching. Do not adopt an owner-user portable directory or copy its demo capability into a public service configuration.

Before any physical ACL operation, prepare fixture-tested ACL plans for a dedicated reviewed native secret/config path: operator write, only the required service identity read, no ordinary client read/write. LocalService access must be proven on that path under the real identity, not inferred from an administrator test. Source and Python/runtime inventories are read/execute-only to the service; writes are restricted to its reviewed state/log paths. Existing installed market, office-machine and foreign-cluster ACLs remain outside the change.

Keep DSNs/tokens out of argv, stdout, public pages, receipts and audit bodies. Bind the secret version to a redacted configuration receipt. Define operator-mediated credential issue/rotation/revocation and expiry behavior; no silent replacement of the demo's one-hour token, infinite grant or continued access after revocation. Browser capabilities remain separately persisted actor/action/artifact grants, not the service's database password.

Sanitize inherited Python/TESTER/libpq overrides and user-site imports. Launch only the fixed reviewed source/runtime entrypoint; do not install dependencies at service start. Pin runtime psutil explicitly where required: it is currently a development extra although portable process ownership checks import it.

## Source/version and OS-authenticated identity receipts

Freeze a candidate manifest containing full commit/tree, archive SHA-256, complete source inventory, actual F/G origin pins, SQL checksums, Python/native dependency hashes, installation root/ID, app service identity/account, Research DB name/role/endpoint/system identifier, configuration schema/version and active source/runtime pointers.

Retain exact **before and after** source/native/pointer/config receipts. A SHA-named directory and self-reported HTTP SHA alone are not independent verification. Validate immutable files and registered ownership, then bind the real OS process image, PID creation, command/module, cwd and owned accepted TCP endpoint before disclosing a capability. Verify application identity/health separately against those pins.

Do not leak raw unrelated process commands/environment in receipts. Normal service stop/start and fresh instance identity must be covered independently of worker-recovery tests. The existing ban on native recovery termination remains; this addon does not create a recovery action or schedule one.

Any future forced worker or descendant termination requires a separate reviewed source action and explicit authorization naming that termination scope. Install/start, normal stop/restart, upgrade or rollback approval does not implicitly authorize it. A graceful-stop timeout must report the limitation and cannot silently add a kill or recovery retry.

## Health, concurrency and renderer parity

Maintain existing main API/download readiness and degraded-provider behavior. Add explicit versioned Research health (disabled, not provisioned, ready, capability expired/revoked, worker failed) without rewriting the existing market health meaning. Never report SCM Running solely because a subprocess exists. Bound admission, workers, memory, result size, retries and shutdown deadlines through typed source-reviewed configuration.

Authentication/authorization must be rechecked on submit/replay/dispatch/result/ACK. Preserve actor-scoped idempotency, canonical-byte digests, stale-worker fencing and immutable audit continuity across normal restart. Confirm the actual F result/input digests and one-to-one fill/overlay correspondence, including open entries and partial exits. Preserve bar windows, knowledge cutoff, exact decimals and null closed-trade IDs.

Validate real browser rendering against the actual authenticated main-app addon and canonical PostgreSQL, including resize/zoom/keyboard behavior and exact rendered visible fill IDs. ACK is a client assertion about a digest and visible fills; independent browser evidence is still needed. Synthetic source qualification never establishes authentic historical completeness, trader identity or real execution.

## Backup, upgrade and rollback

Before a future approved change, make consistent backups of every owned database in the explicit change scope, including the owned market database if it is affected and the new Research database. Record exact scope, roles, schema/migration checksums and canonical row/artifact/audit hashes. Restore-probe into separately owned disposable databases/destinations and compare evidence. Do not overwrite the source/market cluster as a restore test.

Publish immutable new source/runtime/configuration and retain the exact prior compatible app 719 source/runtime/pointer/configuration receipts. Compatibility must be reviewed before migration. Prefer additive/versioned Research schema; no destructive down migration or deletion of newer research evidence to make old code appear compatible.

Rollback changes **app configuration and active source/runtime pointers only**, stopping/restarting only the exact owned app service when required. Do not stop PostgreSQL, the market cluster, a separate research cluster or unrelated services. Retain the new Research database, immutable artifacts and audit. Restore the exact compatible prior configuration, not merely `research_enabled=false`: old strict configuration models can reject newly added fields. Old app code must be able to resume its original market/download behavior without reading unsupported Research schema.

After rollback, revalidate source/native/config/pointers, process identity, ordinary stop/start, health, existing market/download API parity and the unchanged database identities/data. If compatibility or ownership is uncertain, leave the addon disabled and report rollback NOT_PROVEN. Do not retry UAC, use a broad stop/kill, invoke worker recovery, change main or destroy data automatically.

## Reviewable implementation phases before any activation request

1. Preserve the completed exact A43 CI and physical evidence above. CI repair is no longer pending for A43. Every new implementation candidate must rerun all applicable gates with Linux 3.11/3.14 and Windows results identified separately. Disposable CI `TEST_DATABASE_URL` on CI 5432 is not authority to touch host 5432 and does not replace explicit non-5432 Research integration tests.
2. Implement typed default-off config and main-app addon composition with no research connection/migration when disabled. Fixture-test routing/health parity and missing/foreign/expired source/auth/DB cases before native tooling.
3. Implement explicit operator-only provisioning/migration/secret ACL and versioned receipts. Test least-privilege access and restore probes in disposable environments. Prepare no action against installed ADMINIT as part of these source tests.
4. Test actual F/G/E plus durable PostgreSQL and D browser path under the reviewed main application. Exercise actor isolation, revocation, canonical replay, fenced crash/restart and exact fill ACK without fake successful backends.
5. In a separately authorized disposable Windows native test, verify service-account secret access, normal stop/start/restart, process/instance/source identity, migration compatibility and app-only rollback. Keep PostgreSQL running and preserve all new DB evidence.
6. Prepare the exact concrete native change package: service identity/root, candidate/source/native/pointer/config pins, typed opt-in setting, owned database/role/migration, changed ACL/registry paths if any, backup/restore probe, stop/start health/parity and app-only rollback. Only after the implementation is independently reviewed and all applicable gates pass, request **one fresh explicit approval for that concrete activation package and its exact privilege/native scope**. The present ban on UAC, native upgrade and recovery remains in force until that new approval; this plan supplies none. Forced worker recovery remains a separately authorized action even after activation approval. No automatic scheduling or activation follows from this document.

## CI status and historical diagnosis

A43 is the final passed source baseline above. Earlier Ruff I001, shallow pinned-Git history, missing legacy pytest/dev dependencies, shared `os.name` mutation, PowerShell module/token context, provider-factory fixture origin, readiness-envelope and transient audit-transport failures were historical CI work, not open A43 failures. The current exact-source CI evidence is green; no workflow rerun, vulnerability suppression, guard relaxation or blanket test exclusion is proposed by this plan.

The plan PR must record its own exact documentation-commit CI results separately. Passing those checks only shows this documentation change does not regress the retained source; it cannot implement, approve or satisfy the remaining native/addon gates. Future activation remains NO_GO until the versioned implementation, least-privilege provisioning/secret integration, owned-database restore evidence, resource bounds, real service-account physical validation and exact compatible rollback are independently reviewed and passed, followed by the single fresh concrete approval described above.
