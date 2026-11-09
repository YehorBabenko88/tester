# Same-root cooperative portable restart: blocking protocol

**RESTART_NOT_READY / DESIGN_ONLY. No restart action is implemented by this PR.**

Pinned assessment base: `6a24e50d51f4d65783c4436b94fe87cc883acbeb`.
ROOT reports successful physical preparation/limited-role, owned processes,
real-data readback and synthetic F/G/D/summary/ACK on that candidate. Those are
ROOT's separate evidence; this source-only assessment did not execute or extend
them. Do not interpret a successful shutdown as same-root restart acceptance.

## Concrete current blockers

1. `pc4_runtime.serve` rejects retained API/worker/stop records. Those files are
   intentionally retained. Removing/renaming them to bypass the guard is not a
   restart implementation and would discard ownership evidence.
2. `src.api.worker.run` observes the fixed `runtime/stop.request`. A new child
   would immediately stop if it reused this path. Changing data_dir to evade the
   flag breaks the pinned config and provider-owned-root assumptions.
3. Records/guards use fixed process filenames and exact single-session argv.
   Replacing PID fields, weakening module checks, accepting any listener or
   selecting a process by port would permit adoption/reuse.
4. A stop request is intent, not proof of drained work or successful process
   exit. The current API does not persist a birth-bound external shutdown/exit
   witness. PID absence alone cannot distinguish clean shutdown from a crash.
5. Changing the installed source to add restart contradicts the same immutable
   source requirement. A new candidate must contain this protocol before its
   first enrolled run. The current installed candidate must not be hotfixed or
   retroactively labelled as an enrolled generation.

These seams require a lifecycle protocol, generation-specific worker stop path
and process guards. A bounded source patch that merely clears flags or retries
serve cannot satisfy the requested safety conditions. This PR therefore leaves
the working product/runtime lifecycle unchanged and publishes the blocking
design instead of an optimistic restart command.

## Minimal sufficient future invariants

An explicit operator-only opt-in controller owns generations `0..31`; ordinary
serve remains fail-closed. Each new directory is claimed by atomic mkdir and
each manifest/event/process/stop/closure file is create-only, never unlinked,
renamed, truncated or reused. Unknown files, gaps, duplicate/partial generations,
invalid hashes or exhausted bounds cause refusal, preserving all evidence.

The generation manifest must bind installation UUID/root, immutable source and
lab SHAs, exact market config/seed/role/execution-private hashes, previous sealed
generation hash, original PostgreSQL system identifier plus original PG PID,
birth/image/argv/data_directory, fixed PG55471/API18767 and limited runtime role.
Private credentials stay in their existing owner-only files, never manifests,
argv, logs or HTTP. No migrations, grants or token refresh during restart.

Both initial opt-in start and restart must honor a common reservation before
launching any child. Existing serve must at least refuse a reserved generation;
the normal action must not become an implicit restart. Concurrent controllers
cannot both reserve the same successor. A failed claim/start is retained as a
partial generation and cannot be skipped to manufacture a later clean chain.

### Append-only lifecycle

| Event/state | Necessary proof before progressing |
| --- | --- |
| RESERVED | Unique successor claim; prior CLOSED; same source/config/root/PG incarnation/limited role; no live/reused old PID; API port free. |
| STARTING | Fresh generation-specific API/worker records with PID, OS birth/image/cwd, exact fixed module/argv/config and manifest hash. Old records untouched. |
| RUNNING | Fresh accepted TCP four-tuple proof for the exact new API plus exact worker identity; never listener-only. |
| STOP_REQUESTED | Operator verifies the current generation and obtains birth-bound process handles before create-only generation-specific stop requests. |
| DRAINED | Research call and download worker cooperatively finish; bounded wait never invokes kill/terminate/elevation/SCM. Timeout is refusal, not closure. |
| CLOSED | External observer waits for those exact handles and records zero exit results, stop-request hashes, ownership hashes and completed drain proof; fresh re-read/hash check. |
| NEXT_ALLOWED | All previous closure/manifest links valid, both old PIDs absent, original PG incarnation and unchanged config/source/role still verified; current credential still valid. |

An API writing its own receipt before return cannot alone prove its later death.
A future published shutdown-and-seal observer must acquire exact process handles
while the processes are proven alive and keep them until waits complete. No
retroactive certificate from old log text or a missing PID. AccessDenied,
unavailable process metadata, missing/partial witness, nonzero exit, zombie or
same-birth live process all refuse. A currently occupied old PID with different
birth also refuses as PID_REUSED; do not inspect/stop/adopt that foreign process.

Restart never stops a live prior generation automatically. The explicit
cooperative shutdown-and-seal step must finish first. The operator's own PID or
a fresh child reusing a prohibited old PID is also rejected, preserving the
conservative rule rather than broadening it for convenience.

### Worker/config boundary

The worker needs a separate reviewed opt-in stop-path mechanism bounded to its
generation directory and manifest. Its ordinary fixed stop.request default must
remain unchanged. Retain the original data_dir, DSN, provider root and admission
semantics. New wrapper module/argv require an exact independent allowlist and
OS birth/cwd/image/config checks; do not broaden the old market worker guard.
New process stdout records are generation-specific; do not overwrite prior logs.

The new API may reuse the existing app factories and leased durable jobs, with a
public non-secret generation identity. That is not permission to alter research
inputs, reset job attempts, accept UNKNOWN real coverage, relax artifact/summary
digests or skip existing auth and rendered-fill ACK. Expired credentials, PG
restart/restore/incarnation change, source upgrade or config change require a
separately reviewed lifecycle; this feature must refuse them.

## Gates required before any implementation/physical success claim

Pure RED/GREEN tests must cover live exact PID, reused PID, absent PID without
witness, denied process observation, stale/wrong source/config/root/PG identity,
non-limited role, bad/expired credential, malformed/partial/hash-conflicting
generation, concurrent claims, unexpected files and bounded generation limit.
Positive tests must prove fresh API/worker records and exact argv ownership,
old records/stop files byte-for-byte unchanged and the old stop file ignored only
by the new generation's validated stop mechanism. No fake native proof.

ROOT-only actual acceptance must use a new immutable candidate: enrolled initial
run, real durable job/ACK, verified cooperative seal, same-source/same-PG restart,
new PID/birth/four-tuple proofs, no duplicate records/data, retained old evidence
and another cooperative shutdown. Hosted/default tests cannot certify a native
process merely by accepting a mock. Existing stopped installations are not
silently enrolled; ambiguous legacy or partial state remains blocked.

## Executed evidence for this design-only PR

Five pure regressions call the actual current serve refusal with mocked context
and forbidden socket/process/Popen boundaries: each retained process/stop record
refuses before side effects, keeps its original bytes and creates no new files.
These tests establish existing refusal only. They are not a new restart feature,
not fresh-generation ownership acceptance and not physical PID/PG observations.
