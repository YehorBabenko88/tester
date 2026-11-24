# Owner-operated Windows physical acceptance

This is a replay procedure, not evidence that a reboot or ADMINIT deployment has
occurred. Use an independently disposable Windows x64 VM and its own desktop.
Do not reboot ADMINIT or install over its existing `C:\TesterLab`. Production
deployment remains subject to owner authorization and independent acceptance.

## Before installation

Record the VM identity, Windows build, UTC timestamp and exact published Git SHA. Obtain ZIP, Setup-script and envelope SHA-256 values, the Ed25519 key fingerprint, the raw DER code-signing-certificate SHA-256 and current owner trust/rotation policy through an independently authenticated owner channel.

Before copying or executing package code, use trusted Windows console commands from [security-signing.md](security-signing.md) to create fresh UUID staging with protected SYSTEM/Administrators ownership and ACLs. Copy the ZIP into that protected tree, verify the external ZIP digest on the protected copy, and extract only safe regular paths. Reject reparse points, links and unexpected files; protect every extracted directory and file before verification. Verify the protected Setup byte digest, its Valid Authenticode signature and independently approved raw certificate pin before invocation. Never execute a mutable Downloads copy or import a certificate from the package.

Fixture keys are permitted only in explicitly labelled disposable test-mode procedures. They do not satisfy production Ed25519, owner publisher or AllSigned trust. No production owner key or certificate is available here, so the production signing/AllSigned portion of this procedure remains NOT RUN. Do not bypass execution policy or install test trust to manufacture a production PASS.

Capture existing services' names, accounts, executable paths, startup types and
dependencies; capture listener owners on port 5432 and unrelated sentinel hashes.
Do not stop, modify or delete the system PostgreSQL. Ensure `C:\TesterLab` does not
exist on the VM. If it exists, stop and choose another disposable VM. Preserve
evidence outside the installation, without credentials or market-data backups.

After the complete trusted staging/bootstrap checks above, run an elevated trusted owner console against the protected signed package. Substitute independently verified pins, never values copied from the package being authenticated:

```powershell
powershell.exe -NoProfile -ExecutionPolicy AllSigned -File '<protected-staging>\bundle\Setup-Tester.ps1' `
  -Bundle '<protected-staging>\bundle' `
  -ManifestSHA256 '<external-envelope-sha256>' `
  -TrustedPublicKeySHA256 '<owner-ed25519-key-sha256>' `
  -TrustedPublisherSHA256 '<owner-raw-certificate-sha256>' `
  -ExpectedSourceSHA '<published-source-sha>' -Mode production
```

AllSigned script authentication and Ed25519 full-payload authentication are separate gates. A fixture-signed review ZIP cannot be substituted into this production command. The disposable test-mode CI fixture uses its explicit UUID root/fake desktop and cannot establish canonical production installation, trusted owner signing or reboot acceptance.

Record the installation UUID and names from `.tester-release.json`. All subsequent
service operations must use those exact, verified names. The expected configuration
is exactly two owned automatic services, PostgreSQL preceding the application,
unique virtual accounts, two bounded restart actions and no action thereafter.
Keep `GUARDIANS_ENABLED=false`; use public read-only exchange requests only.

## Runtime and reboot matrix

1. Inspect both real COM shortcuts, including their Russian names, target,
   arguments, working directory and elevation flag. Require exactly **TESTER —
   Запуск** and **TESTER — Удаление** for this installation.
2. Launch through **TESTER — Запуск**; require the terminal to open and the API
   identity to match the installed source/instance. Verify database, worker and
   storage readiness independently of provider availability. Launch again and
   prove the service and child process identities did not multiply.
3. In the terminal, select an instrument, load a bounded public historical range,
   inspect OHLCV/provenance/checksums, submit a test job and inspect its durable
   result. Record exact counts/digests, job IDs and truthful coverage status.
4. Kill only the verified owned worker process. Confirm bounded recovery and
   durable checkpoint/lease fencing. Kill the verified owned API child; verify
   recovery and repeat browser reads. Exhaust the budget in a separate controlled
   run and prove it stops instead of looping indefinitely.
5. Stop owned services gracefully; restart via the launch shortcut and compare
   saved data/result digests. Repeat the same verified package installation and
   require idempotence. A different-source upgrade requires separate migration
   acceptance; same-release repetition does not certify upgrades.
6. On the disposable VM only, have the authorized operator reboot Windows. Record
   boot identity/time before and after. Without launching a shortcut, prove both
   services start automatically and recover readiness. Re-read the saved data and
   job results and compare their digests. Automatic startup configuration alone
   is not reboot evidence.

## Removal and clean reinstall

Launch **TESTER — Удаление**. Inspect the displayed resource list. Test an incorrect
confirmation first and prove no resources changed. Then authorize destruction of
this disposable installation by entering exact `DELETE TESTER`, followed by `YES`.
Require the full completion receipt only after transient maintenance cleanup.

Verify the installation root, both service registrations, owned cluster/data,
configuration, history, results, logs, queues, checkpoints, backups, temporary
maintenance runtime, trust configuration and both shortcuts are gone. Inspect
owned scheduled-task/autostart receipts if any were created. Durable diagnostic
receipts outside the root must contain no retained installation/user data.
Recheck unrelated sentinels, service configurations and the system-5432 listener
identity against the initial snapshot. Do not equate naturally changing live
system-database bytes with installer mutation.

Repeat a clean installation from the same verified package, repeat readiness and
one durable job, then remove it again. Attach command exit codes, source-bound
test reports, browser traces, SCM snapshots and redacted receipts to Issue #88.
Report each unperformed step as NOT RUN, each platform skip with its reason, and
each failed assertion as FAIL. Request independent MASTER/four-Guardian decisions
before asking the owner to authorize production publication or deployment.
