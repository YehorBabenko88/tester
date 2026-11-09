# Published opt-in combined portable runtime

`src.integration.pc4_runtime` is a separate operator entrypoint. Ordinary
`market_runtime` and product/native defaults are unchanged. The exact published
candidate must combine this PR63 with PR61 snapshot modules and PR62 frontend.
The harness refuses missing dependencies instead of injecting local patches.

This is EXPERIMENTAL_PORTABLE, not a Windows service or native ownership proof.
Only ROOT/PC4 operator may execute these commands after physical preflight and
immutable source archive/file-manifest verification. Other agents run pure tests
only. There is no installation, UAC, service registration, external connection,
process adoption/kill, default PG5432 access or automatic schema change.

Use a **new** research_lab-owned root/cluster created by the existing approved
lab procedure, with owner-only root ACLs, PG55471 and API18767. Keep all previous
roots/files/database evidence. The new source directory must be named with the
full published candidate SHA. `LAB_SHA` identifies the marker creator; `APP_SHA`
is that new candidate, not this document's parent commit. All runtime commands
must execute from that exact source directory. Preparation is deliberately
single-use: partial failure preserves files and refuses resume/adoption.

```
python -B -m src.integration.pc4_runtime prepare --root ROOT --lab-source-sha LAB_SHA --application-source-sha APP_SHA
python -B -m src.integration.pc4_runtime limit-role --root ROOT --lab-source-sha LAB_SHA --application-source-sha APP_SHA
python -B -m src.integration.pc4_runtime serve --root ROOT --lab-source-sha LAB_SHA --application-source-sha APP_SHA
python -B -m src.integration.pc4_runtime verify --root ROOT --lab-source-sha LAB_SHA --application-source-sha APP_SHA
python -B -m src.integration.pc4_runtime shutdown --root ROOT --lab-source-sha LAB_SHA --application-source-sha APP_SHA
```

Prepare reuses market's fresh-root/fresh-database seed and its existing refusal
of previous synthetic roots. It explicitly migrates the research ledger (002)
and B snapshot repository, publishes only the exact pinned synthetic snapshot,
registers the pinned DSL and grants that immutable input to one principal.
The PC4 composition explicitly adapts the already verified canonical market DSN
for research only: exact fixed loopback hostaddr must equal host, fixed port/user
and canonical key set must match, and only hostaddr is removed before the strict
public research validator. Research connections re-pin that literal address
internally. Market config/DSN/seed bytes and privileged operator connections keep
their original hostaddr; the public research safety policy is not relaxed.
This fixes ROOT's retained `efd04cf` preparation failure after market seed, before
research DDL. Installed failed sources/lab remain immutable and are not retried;
the corrected source must be published into a new immutable candidate/new lab.
The source is the separate terminal-compatible byte-pinned SYNTHETIC linear
BTCUSDT fixture from PR61 4d9f365c44fb401fe051b48f4938a3674c058d9c, via
publish_terminal_fixture() and certified_terminal_fixture_strategy(); the original
SPOT fixture is never relabelled. Its four bars and explicitly hypothetical
required funding schedule/event are source inputs, not real exchange history.
The actual F fixture run records funding_costs `0.0100`, retaining all uncertainty.
Its token is returned only into owner-root `execution-private.json`, never stdout
or URL; the receipt binds its file hash. Token lifetime is one hour, not refreshed
automatically. The operator may supply its token/input_digest to the UI privately;
never include that file in logs, screenshots, manifests or uploaded artifacts.
The existing `private.json` remains the lab's PostgreSQL secret; no DSN is printed.

Before serve, the explicit `limit-role` step is mandatory. It creates a separate
operator-only tester_pc4_admin superuser with a fresh secret retained solely in
owner-root operator-admin-private.json. Both bootstrap and administrator verify
the owned data_directory/database/port and matching PostgreSQL system identifier
before DDL. In the administrator transaction, research_owner is renamed to
tester_pc4_bootstrap (its owner OID and SU flag stay intact), and its LOGIN is
disabled: leaving the renamed same-password SU login usable would bypass runtime
restrictions. A NEW limited research_owner uses the existing password, so all
runtime configuration/seed/DSN bytes remain unchanged.

The new role gets only DML/sequence access to the five runtime schemas, SELECT
on qualified snapshots, and pg_read_all_settings for owned directory verification.
It owns neither database nor schema/table, has no elevated role flags, no CREATE
or TEMP, and no other role membership. PUBLIC CREATE/TEMP are revoked only in
this test-owned database. Runtime context/serve checks these restrictions and
refuses bootstrap/SU, database/schema/class/function/type ownership, unexpected
memberships or CREATE on **any** schema (including unexpected schemas), not only
the named application schemas. The hosted-PG regression provisions a retained
probe schema, validates the limited baseline, grants CREATE to prove refusal,
revokes it and proves recovery, without DROP or an active application server.
No foreign roles/databases are changed. Partial role handoff retains the private
stage/secret and refuses automatic resume; ROOT investigates with its private
operator credential. The admin secret is written exactly once and never rewritten
for stage updates; sanitized progress is separate in pc4-role-progress.json, so
a progress-write failure cannot truncate the only administrative credential.
Never upload either private file or expose either DSN.

Serve verifies the source/root/seed, PostgreSQL process image/PID/argv/birth and
actual data_directory via unchanged market context, and current execution grant.
It starts ordinary market API/catalog/download handling and an owned child
`src.api.worker`, plus one in-process research polling task for the one granted
principal. No startup migrations/publication take place. F/J input remains
SYNTHETIC_ONLY; real-market catalog/download coverage is never copied into it.
Research completion returns the existing authenticated chart Command/ACK path.
Both execution and chart jobs are polled; current credentials and leases are
checked by their stores. Repeated polling failure stops research after three
errors with finite public status at `/api/pc4/identity`. It exposes no credentials.

Verify requires exact process records, image/birth/cwd/module/argv/config and the
existing strict accepted TCP four-tuple observer; no listener-only substitution.
It separately verifies the owned download child. Credential expiry does not
disable physical process verification or cooperative shutdown. The PostgreSQL
ownership guard still must be available; shut down API/worker before PG.

Shutdown verifies owned API/worker and writes `pc4-shutdown.request`; the server
observes it and exits cooperatively. Research drains its current bounded F call
(70-second wait; stall reports refusal, never force-kill). The download child
receives its existing stop.request and bounded wait. Unexpected download-worker
exit stops the API instead of silently presenting an active worker. Process/stop
records and all data are retained, so serve refuses same-root process restart or
adoption. Durable job restart semantics are separately tested by ledger reopen;
this harness does not claim physical same-root restart acceptance.

Pure regression evidence: module-absent/role-validator/secret-rewrite RED; terminal
pair is also tested against exact producer raw Git source with genuine F execution.
The broader runtime/market/research/argv/execution/summary pure regression suite
passed (85 PASS including nine DSN-boundary regressions); five actual PG/HTTP/role
cases remain hosted-only and were not run locally.
Ruff check/format passed. No actual API/PG/process/browser/SCM
execution by this contributing agent. Root must review and run the new immutable
candidate, including real PG ledger first and no-route-mock browser acceptance.
Full connected download→qualified-real-F history, native and real trading remain
unproved/disabled; this operator harness does not turn source review into success.
