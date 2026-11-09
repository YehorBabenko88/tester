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
python -B -m src.integration.pc4_runtime serve --root ROOT --lab-source-sha LAB_SHA --application-source-sha APP_SHA
python -B -m src.integration.pc4_runtime verify --root ROOT --lab-source-sha LAB_SHA --application-source-sha APP_SHA
python -B -m src.integration.pc4_runtime shutdown --root ROOT --lab-source-sha LAB_SHA --application-source-sha APP_SHA
```

Prepare reuses market's fresh-root/fresh-database seed and its existing refusal
of previous synthetic roots. It explicitly migrates the research ledger (002)
and B snapshot repository, publishes only the exact pinned synthetic snapshot,
registers the pinned DSL and grants that immutable input to one principal.
Its token is returned only into owner-root `execution-private.json`, never stdout
or URL; the receipt binds its file hash. Token lifetime is one hour, not refreshed
automatically. The operator may supply its token/input_digest to the UI privately;
never include that file in logs, screenshots, manifests or uploaded artifacts.
The existing `private.json` remains the lab's PostgreSQL secret; no DSN is printed.

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

Pure regression evidence: module-absent RED then 28 PASS for runtime, argv and
execution tests; Ruff check/format passed. No actual API/PG/process/browser/SCM
execution by this contributing agent. Root must review and run the new immutable
candidate, including real PG ledger first and no-route-mock browser acceptance.
Full connected download→qualified-real-F history, native and real trading remain
unproved/disabled; this operator harness does not turn source review into success.
