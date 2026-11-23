# Owner-operated Windows physical acceptance

This is a replay procedure, not evidence that a reboot or ADMINIT deployment has
occurred. Use an independently disposable Windows x64 VM and its own desktop.
Do not reboot ADMINIT or install over its existing `C:\TesterLab`. Production
deployment remains subject to owner authorization and independent acceptance.

## Before installation

Record the VM identity, Windows build, UTC timestamp, exact published Git SHA,
installer ZIP SHA-256, independently supplied envelope digest and owner public-key
fingerprint. Verify the ZIP against that external receipt and extract to a fresh
directory. A fixture key is allowed only for explicitly labelled disposable tests.
It cannot establish production trust.

Capture existing services' names, accounts, executable paths, startup types and
dependencies; capture listener owners on port 5432 and unrelated sentinel hashes.
Do not stop, modify or delete the system PostgreSQL. Ensure `C:\TesterLab` does not
exist on the VM. If it exists, stop and choose another disposable VM. Preserve
evidence outside the installation, without credentials or market-data backups.

Run elevated PowerShell from the extracted package, substituting independently
verified values (never copy trust values from the package being authenticated):

```powershell
.\Setup-Tester.ps1 -ManifestSHA256 '<external-envelope-sha256>' `
  -TrustedPublicKeySHA256 '<owner-key-sha256>' `
  -ExpectedSourceSHA '<published-source-sha>'
```

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
