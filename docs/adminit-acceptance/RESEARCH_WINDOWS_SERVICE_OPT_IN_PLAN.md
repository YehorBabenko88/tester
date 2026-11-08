# Opt-in Windows Research Lab service — review plan only

**Status: DOC_ONLY / NOT_IMPLEMENTED / NOT_AUTHORIZED_FOR_NATIVE_ACTIVATION.**
Requested after the synthetic vertical acceptance and PR #39 CI failures. This
document prepares a future, separately reviewed service implementation. It does
not register/start/stop a service, change ACLs or registry, access installed
ADMINIT, use PostgreSQL 5432, invoke UAC, perform recovery or authorize deployment.
The root coordinator remains the sole physical operator.

## Source baseline and current behavior

Reviewed application source `68e59b5` (exact full SHA must be resolved in the
eventual release manifest), with subsequent CI-only import/format fix `8a0e894`.
The relevant current files are `src/integration/research_lab.py`,
`research_runtime.py`, `src/api/research_ledger.py`, `research_control.py`,
`research_worker.py`, the research SQL migration and pinned F/G producer manifests.

The working composition is explicitly portable: a fresh non-service research
cluster and `research_runtime serve`, a loopback API plus one in-process polling
task. Its identity truthfully reports `native_service=false`. It is not imported
by the installed service. `src/api/lab_service.py` currently launches only
`src.api.supervisor`, uses the existing lab marker and fixed 18766/55470 service
contract, and cannot be repurposed by changing a registry string. The older
`windows_service.py` is also a download-supervisor host, not a Research Lab host.

The portable marker, user-only workspace ACL, bootstrap database owner and
one-hour capability are not a finished unattended-service contract. Directory
name equality in `require_published_origin` is an origin guard, not independent
full source/inventory verification. All of these differences require explicit
implementation and tests before native activation.

## Proposed scope and ownership

Use a new opt-in Research Lab service and marker schema. Keep the installed
ADMINIT service, its root, native inventory, app/API ports, PostgreSQL cluster,
data, registry and account permissions outside its ownership. Do not adopt a
portable marker or move a user workspace under a service account automatically.

The future review should freeze these decisions in a typed service plan:

| Boundary | Required decision/behavior |
| --- | --- |
| Installation | Explicit new dedicated root and installation UUID; reject an occupied root, reparse path, unowned marker or foreign service name |
| Service identity | Names such as TesterResearchApp_<uuid> and, only if separately included, TesterResearchPG_<uuid>; no generic service-name matching |
| Account | Reviewed least-privilege service identity; no LocalSystem/default administrator runtime |
| API | Loopback 127.0.0.1:18767 only; conflict refuses activation without a fallback port |
| Research database | Explicit owned tester_research_<uuid> database/cluster, loopback 55471; no reuse of 5432, 55470, installed tester_lab or arbitrary DSN |
| Existing portable lab | Remains retained; if it occupies Research ports, report a conflict. A separately authorized graceful owner-scoped stop or explicit alternative design is needed, never a broad kill |
| Code | Immutable SHA release directory and exact source/runtime inventory; read-only to service account; no imports from worktrees, user-site packages or arbitrary plugins |
| State | Canonical PostgreSQL only for jobs, grants, immutable bundle/artifact, ACK and audit; no parallel SQLite or success-state file ledger |
| Physical operation | Separate install/start approval after exact implementation, manifest, backup and rollback review |

The source adapter remains the real pinned F engine and the actual G typed
client. No engine execution is accepted from an arbitrary HTTP payload. A future
seeding/import command must publish a digest-verified bundle through the real
replay verifier, with its own operator authorization. Synthetic source namespaces
remain SYNTHETIC_ONLY / real_market_coverage NOT_PROVEN.

## Version, manifest and launch contract

Before enabling a service, publish a release manifest binding the complete
repository commit/tree, archive hash, complete file inventory, F/G source pins,
SQL migration checksums, reviewed service-host SHA, Python/pywin32/psycopg/psutil
and other runtime wheel hashes, installation ID, root, service names, account,
ports and PostgreSQL system identifier. Pin psutil explicitly for the runtime:
it is currently a development extra even though portable process ownership
verification imports it. No dependency installation should occur at service start.

The service must validate its registered identity against its new marker, source
manifest and actual binary/runtime inventory. Build identity returned by HTTP
does not substitute for OS process ownership. The launch entrypoint must be a
fixed reviewed module and fixed typed arguments, not a caller-provided shell,
Python expression, SQL command or provider path.

Sanitize inherited Python/TESTER/libpq overrides; inject only the owned reviewed
configuration. Tokens and DSNs stay outside command lines, stdout, audit payloads
and Git evidence. Hide background windows. Use a reviewed owned descendant/job
lifetime boundary; never terminate a process selected only by name or PID.
SCM running status follows source validation plus actual API/database/worker
readiness, not merely successful process creation.

## Access control and capability lifecycle

Prepare explicit directory ACL fixtures before applying any ACL. The service
account needs read/execute only on source and runtime; write access only to its
specific state/log locations and reviewed database credential material. Existing
ADMINIT and office-machine ACLs must remain untouched. Root-owned source pins and
private configuration must not be writable by the API actor.

Separate bootstrap/migration ownership from the long-running database role.
Portable `research_owner` is created by initdb as a bootstrap owner; do not
silently treat that role as production least privilege. Review the necessary
table/sequence privileges and immutable evidence triggers, then test denied DDL,
foreign schemas/databases and data mutation. Migrations run only as an explicit
reviewed operator action, never automatically from HTTP or service startup.

Keep authentication and current object/action grants persisted and rechecked on
submit, replay, dispatch, retrieval and ACK. A browser capability is not a service
password and must not be embedded into a public page. Define operator-mediated
credential issue/rotation/revocation, expiration handling and service behavior
after expiry. The current one-hour seeded capability cannot justify silently
issuing a replacement or retaining access after revocation.

Host/Origin/CSRF policy must be reviewed for the actual service browser/client
flow; preserve the current typed loopback restrictions. Before a client discloses
its capability, verify the exact owned process image/creation/source/arguments
and accepted TCP endpoint, then independently verify the application identity.
ACK records the exact artifact digest and rendered visible fill IDs; it remains a
client assertion with separate real-browser evidence, never independent proof of
pixels or real trading.

## Graceful stop, upgrade and rollback

Implement an explicit Research-only draining/stop protocol. Stop new admissions,
finish or release/fence owned leases, cancel the in-process polling task, close
the owned HTTP listener and report SCM state within reviewed deadlines. Do not
reuse the ADMINIT download stop.request protocol without a reviewed adapter. Do
not stop PostgreSQL merely because the API stops. Abnormal process containment
must target only the new service's verified descendants; native worker-recovery
termination is not part of this plan.

An upgrade is a separately approved transaction over a new immutable release and
new private runtime inventory. Retain the previous source/runtime/configuration
and a tested backup of this Research database. Record exact registry/SCM config,
ACLs, marker bytes, PostgreSQL system identifier, migration checksums, canonical
row/artifact/audit hashes and service identity before any physical change.

Compatibility must be decided before migration: a code rollback cannot undo an
incompatible schema. Prefer additive/versioned migrations and reject a rollback
whose old code cannot read the retained schema. Any restore requires its own
explicit data-loss review; never overwrite the installed ADMINIT cluster or
silently delete newer research artifacts/audit to make a downgrade succeed.

On failed upgrade, fail closed, stop only the newly owned Research app if
necessary, preserve diagnostics and switch back to the retained compatible
release/runtime/config with explicit identity checks. Revalidate canonical data
and readiness. If identity or schema compatibility is uncertain, leave the
Research service stopped and report rollback NOT_PROVEN; no automatic privilege
retry, broad service stop, recursive deletion or main merge.

## Implementation and acceptance gates before an activation request

1. Repair current PR #39 CI from observed logs. Record Linux 3.11/3.14 and Windows
   results separately. Research database tests require an explicit disposable
   non-5432 TEST_RESEARCH_DATABASE_URL; existing platform CI's 5432 database must
   not be substituted to remove skips.
2. Implement source-only typed service plan/host/bootstrap, marker, environment,
   account/ACL and rollback checks using mocked SCM and temporary fixture roots.
   Reject wrong root/ID/source/hash, unknown files, unsafe paths, occupied ports,
   foreign service/account/DB, PID reuse and stale capabilities. Publish and
   independently review the exact source candidate before physical tooling.
3. In a disposable Windows environment, test native install/start/drain/stop,
   bounded crash/restart and upgrade/rollback against a separately created owned
   research cluster. Use actual F/G/E, durable PostgreSQL and real renderer;
   no successful backend substitutes. Restore a backup into another disposable
   destination and compare immutable bundle/artifact/audit evidence.
4. Verify denied/expired/revoked credentials, actor isolation, idempotency
   conflicts, fenced stale workers, scoped retrieval, all-fill correspondence,
   half-open windows/cutoff, exact ACK and actual browser rendering. No real
   market completeness, disclosed trader identity or real-order claim follows
   from the synthetic fixture.
5. Prepare a concrete activation package listing exact service names/root,
   account, manifests, approved DB scope, changed registry/ACL paths, backup,
   compatibility and rollback commands. Only then may the coordinator request
   the separate native/elevation authorization. This document grants none.

## Current diagnosis limits

The supplied Linux job 128799792170 at source 68e59b5 stops at Ruff I001 in
`tests/platform/test_research_postgres.py:173`. It never reaches pytest, lifecycle
or security checks. Root owns that lint/format correction. No additional Linux
runtime failure has been reproduced from that log; future CI logs must determine
the next fix. This plan was prepared solely from Git source and the provided CI
log, with no installed path, service, registry, API or database access.
