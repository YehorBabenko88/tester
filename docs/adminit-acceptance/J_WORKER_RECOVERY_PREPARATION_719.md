# Worker recovery preparation for 719 — source review only

Prepared at the coordinator's request after Issue #32 source qualification.
**NOT RUN / NOT AUTHORIZED FOR TERMINATION.** The root coordinator alone handles
physical ADMINIT work. The canceled TOOL509 UAC action must not be repeated.
The completed authorization for a 719 upgrade does not authorize another UAC
prompt, a worker termination or this recovery test. No installed path, registry,
service, process, API or database was accessed during this review.

## Exact compatibility

The recovery script at both source commits below is the same Git blob:

| Identity | Exact value |
| --- | --- |
| TOOL509 source | `5098fa31f7b624cca968e239ac3c8ace8d4ba32a` |
| Application source under review | `719b02eea4134730b5d688143022706a9cae8dbe` |
| `scripts/windows/Test-TesterLabWorkerRecovery.ps1` Git blob | `68aa6cf5de8830fd13792cb893471a45aeb08ab6` |
| SHA-256 of Git LF bytes | `12f17dab7ac2676d360c3b0cb6080f3cb7ed067c3d97c519eddabd2eb7a7354b` |
| SHA-256 of Windows CRLF checkout bytes | `c6b1d1bb7b4245b7657fa5721502e073e6422a02320a0646ed445e814eb6d176` |

The committed compact upgrade receipt records that CRLF script pin, the 719
deployment manifest hash
`a1572b25fffd82ea5a4650ba57e76b5c66026a24f85d7f7149467c7f719552d2`
and private native manifest hash
`004f9ed3b97f760e3936ac425d4d0d62e0cdcea61d8c5823dd95bbbdddefb317`.
These are recorded preparation pins, **not newly verified installed receipts**.
Future physical preparation must verify the exact immutable artifact bytes;
never normalize line endings or recompute a manifest to accept a mismatch.

The old `worker-recovery-tool-manifest.json` binds application `7b59b415...`,
deployment `b4438d10...` and native runtime `0f441d54...`. It is not compatible
with application 719. The script's external-tool gate correctly rejects those
old application pins. A separate hash-bound manifest would be necessary for any
new external tool. Using an exact published 719 script does not require the old
external TOOL509 manifest: its default gate compares its own bytes against the
719 deployment inventory. This describes source behavior, not an instruction to
launch either path now.

## Existing guards and evidence limits

The script requires an administrator context but never elevates itself. Source
preflight binds owner marker, fixed installation ID/services/ports, source SHA,
deployment inventory, private runtime inventory, registry, API and native
identity. Selection requires exactly one owned leaf worker with the expected
module command, executable image and creation-ordered supervisor/service ancestry.
The held handle is compared against image and creation time; a fresh graph and
service identity are checked before its sole native termination call. No SCM
stop or PostgreSQL stop operation exists in this script.

After termination it requires a replacement worker and the same job reaching
completed with at least two attempts. The job telemetry must report measured
1,440 ingested and verified rows and bytes received. Scoped read-only SQL checks
1,440 raw rows, unique keys and projections; a second API job must preserve the
post-recovery row/projection digest and add exactly one matching job. This proves
bounded replay behavior if actually observed, not historical completeness. It
does not promote UNKNOWN market coverage or qualify F admission.

The test chooses the previous UTC day at execution time rather than accepting a
date parameter. A future approval and evidence plan must explicitly cover the
actual recorded `[start_ms,end_ms)`; today's 2026-11-07 fixture cannot silently
stand in for a later UTC day. Its 45-second running-job opportunity is bounded.
If the job finishes before verified selection, the correct result is NOT_PROVEN;
do not slow the application, add jobs, or retry termination to obtain a pass.

There are two preparation gaps to resolve before treating the existing script as
fully reviewable physical evidence:

1. `termination_count=1` is assigned only after `Invoke-RecoveryTermination`
   returns. If native termination succeeds but `CloseHandle` then fails, the
   durable failure report can still say zero. A zero count from a launched tool
   is therefore not independent proof that no worker was terminated, and must
   never justify another attempt. A reviewed follow-up should distinguish
   attempted, confirmed and indeterminate outcomes and preserve no-retry behavior.
2. The fresh held-handle guard rechecks service/API identity and process ancestry,
   but does not rerun the owner-marker and source/runtime inventory checks from
   preflight. If the required contract is rejection of a marker/source change
   immediately before termination, add a bounded mocked adverse test and reviewed
   revalidation before considering a new tool pin. This document does not claim
   the existing initial preflight supplies that stronger freshness guarantee.

## Safe sequence for the coordinator after a separate future authorization

1. Keep this preparation source-only now. Preserve the canceled TOOL509 receipt.
   Resolve the reporting/freshness gaps through a separately reviewed source pin
   if the physical evidence contract requires them. No installed change follows
   from this document or PR.
2. Freeze the chosen application/tool SHA and raw script/deployment/native
   manifest hashes. Review the exact immutable artifacts and their inventory;
   do not edit the installed tree or reuse the old external manifest.
3. Obtain explicit authorization covering the concrete single-worker termination
   and required privilege context. No self-elevation or canceled-prompt retry.
4. Under that authorization, fail closed on any owner, source, manifest, identity,
   executable, command, ancestry, PID-creation or unique-worker mismatch. Keep
   app/supervisor/PostgreSQL alive. At most one verified held worker handle may be
   terminated; an ambiguous result ends the attempt.
5. Record same-job recovery, replacement worker, attempt count, actual range,
   1,440-row verification, replay job and stable scoped row/projection digest.
   Preserve the redacted durable receipt and SHA-256. Missing evidence means
   NOT_PROVEN, not an automatic rerun. Do not claim F/market completeness.

## Source-only verification performed

The four existing PowerShell helper fixtures passed from the assigned J tree:
`python -B -m pytest tests/platform/test_lab_worker_recovery.py -q`.
The script Git blob and test Git blob match 719 exactly. Fixtures dot-source
`-FunctionsOnly`, mock native process handles and the fresh-candidate function,
and use synthetic process objects and temporary manifest paths. They do not call
the main recovery flow, CIM, SCM, installed HTTP/SQL or native termination. Thus
they establish guard behavior only; they do not establish actual topology,
current manifest identity, same-job recovery or physical acceptance.
