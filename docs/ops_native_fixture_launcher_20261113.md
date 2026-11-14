# Disposable Windows launcher integration — Issue #60 MASTER 20:44

**Status:** Source-only, opt-in fixture. **Native installed PC4 restart BLOCKED.**

`scripts/ops_native_fixture_launcher.py` connects existing components in
a complete *disposable subprocess* flow:

1. Read pinned source/config/root and PostgreSQL-incarnation + lease states.
2. Atomically create a generation reservation, before spawning anything.
3. Spawn exact trusted worker/API fixture argv in an isolated Windows CI root.
4. Retain original `subprocess.Popen` Win32 handles; birth-attest both using
   process creation time, image, cwd, argv, Windows user and distinct markers.
5. Create only generation-scoped cooperative stop requests.
6. Wait on original handles; externally seal both zero exits plus matching
   role-specific drain/checkpoint digests.
7. Revalidate the PostgreSQL-incarnation and live leases, then mark reservation
   closed, allowing the same controller to initiate the next generation.

**Crash behavior:** after a controller crash, the original Win32 handles are
not available to a new controller. The new controller must REFUSE restart,
even if a closure JSON and PID look plausible. This is safe failure, **not**
automated crash recovery. Enrolling an already running installation is
forbidden. A production-grade recovery design would need an independent,
protected, persistent observer/handle owner established before the very first
start; it is intentionally NOT implemented here.

The CI-only launcher requires `GITHUB_ACTIONS=true` and Windows and never
accepts installed PC4 as a target. Its PostgreSQL probe is injected in
Windows tests (fixture), whereas the separate `portable-pg` job uses
real disposable PostgreSQL 16 at host port 55479 to test PG incarnation,
lease fencing, checkpoint and queue recovery. Windows+PostgreSQL physically
combined native acceptance is still **NOT_RUN**.

Safety limits: no `kill`, `terminate`, elevation, UAC, reboot, SCM,
`OpenProcess(pid)`, port 5432, installed PC4 or main merge. Partial
reservation is retained forever on failure. The attempted rollback is
cooperative-only and cannot authorize retrying a partial generation.

**Acceptance gate:** the hosted Windows fixture test must prove two native
generations and a new-controller refusal with real original Win32 handles;
Linux unit tests must refuse active lease/changed PG/partial reservations;
disposable PostgreSQL test must prove its true PG identifier and active-lease
refusal. Never mark installed PC4 native restart PASS from these fixtures.

Rollback: discard this opt-in PR branch. No physical installation changed.

## Mandatory typed original-handle proof at reservation seal

The native reservation seal now requires the actual `NativeExitWitness`
instance plus its retained external exit receipt; the caller can no longer
pass two bare booleans claiming that handles exited and leases were fenced.
The admission layer invokes `witness.require_restart_allowed(receipt)`
again, checks both roles' pinned configuration digest, probes PostgreSQL
incarnation and verifies every durable lease is non-running before writing
create-only `closure.json`. Its closure links the SHA-256 of the validated
external witness, the reservation and the exact PostgreSQL incarnation.

Pure unit tests inject a deliberately limited fake witness and therefore do
not alone certify Win32. The separate Windows fixture uses the real original
Win32 handles while the independent PostgreSQL hosted job provides real DB
proofs. A single combined native Windows+PG E2E remains **NOT_RUN/BLOCKED**.


## MASTER 2026-11-14 05:58 follow-up — post-spawn fence and crash inventory

The hosted OPS workflow at `ae3d0de953b3b5ce35b6717598ac3b820c665713`
had **117/117 PASS** (102 pure, 8 actual PostgreSQL16, 7 real Win32).
This was isolated disposable acceptance, not a PC4 restart.

An important time-of-check/time-of-use race was found in the CI launcher:
PG incarnation and lease state were checked during generation reservation,
but a lease or PostgreSQL restart could occur while worker and API were
birth-enrolling. `DisposableWin32Launcher._post_spawn_fence()` now repeats
PG system ID/postmaster birth verification and requires quiescent lease
states after both children have real original-handle birth attestations.
The witness then re-observes BOTH children before returning the pair.
If the post-spawn fence fails, `rollback_partial_start` cooperatively stops
the *already enrolled* roles, retains hashed drain/exit evidence and permanently
blocks the incomplete reservation; it never force-kills or reuses the
generation. No filesystem preflight can claim to be an atomic PG lease lock.

The CI-only `sample_pair` read-only API brackets each process CPU/RSS/I/O
sample with exact original Win32 handle and OS identity checks. It explicitly
marks hard limits NOT_ENFORCED and atomic PG+process snapshot FALSE. WAL is
measured separately by the existing isolated PostgreSQL resource workflow.
`read_admission_pg` now reads PG system incarnation and job lease counts
within one REPEATABLE READ, READ ONLY transaction (still not an atomic
cross-process startup lock).

`NativeAdmission.inspect_crashed_controller` provides a read-only
controller-crash inventory: immutable generation/closure digest continuity,
PG incarnation and any verifiable active leases. It never reuses a PID,
imports a saved JSON closure as Win32 exit proof, changes job states or starts
processes. Missing PG active lease count is UNKNOWN, not zero. Even a
hash-consistent closure cannot replace lost original handles: after controller
crash native restart remains BLOCKED pending an independent persistent
handle-owning guardian established *before* startup.

New focused tests include real Windows child-process PG incarnation/lease
changes after both births, automatic cooperative partial rollback with
permanent reservation refusal, birth-bound resource collection and
independent real disposable-PG admission-state transition checks. Pure
tests cover corrupted/incomplete crash records, unavailable PG and
unverifiable lease counts.

**Remaining release blockers:** no integrated installed `pc4_runtime`
generation-scoped API/worker stop, no protected persistent external handle
owner across controller crash, no atomic startup lease reservation spanning
PostgreSQL and Windows process creation, no combined physical native Windows
+ PostgreSQL acceptance, no sustained real Bybit workload or hard CPU/RAM/I/O
caps. Installed PC4 stays untouched and BLOCKED; neither these source changes
nor successful prior CI implies release GO.

## MASTER staging #77 source contract preflight — 2026-11-14

The **new** `scripts/ops_candidate_compatibility.py` is a non-activating,
read-only source scanner for the exact candidate producer interfaces.
Its independent CI job checks out immutable DATA #72
`f78349b1ad7bb754baee380f59bca67f2b1d2009`, RESEARCH #71
`b11b79832d1b0d848773ff077a36e7cb0595fdcb`, and UI #74
`fd151168473f4add97ee286b008d4540b49ac566` including UI #70 ancestry.
The frozen PC4 baseline supplies installed `pc4_runtime`, `api/main.py`
and `api/worker.py` for a *static only* review. The job checks each exact
Git commit before reading the corresponding Python sources.

Source contracts identified:
- DATA exports `read_execution_events` / `read_research_trades`;
  RESEARCH exports `read_data_metrics` and the explicit F denial seam;
  the UI source declares `/api/capabilities`.
- Installed PC4 uses `pc4-shutdown.request`, and the original API/worker
  use the unscoped `stop.request`. They do **not** implement OPS-owned
  `OPS_GENERATION` + `OPS_STOP_PATH` + `OPS_CHECKPOINT_PATH` birth-bound
  cooperative stop/exit receipts. No code in PR #73 alters their behavior.
- Even an AST source containing all marker strings is **not** proof of an
  external Win32 original-handle guardian or a real PostgreSQL startup lease
  transaction. The scanner always returns
  `installed_native_activation=BLOCKED`,
  `pc4_restart_authorized=false`, `default_activation_enabled=false`.
- The report hashes the exact source texts and UI API source as a
  reproducible manifest; its hash cannot attest live processes or historical
  source data. `require_installed_native_activation` always rejects.

This static-only composition check does not resolve staging #77's unrelated
OpenAPI Kind enum drift, research `PGHOSTADDR` environment issue or the
combined F/G/D 404 route. Those require separate owner-reviewed changes.

**No auto-enable:** the native launcher remains confined to Windows disposable
GitHub Actions children. It is not imported by installed `pc4_runtime`,
`src.api.main`, or `src.api.worker`. PC4 native restart remains BLOCKED,
regardless of independent producer CI successes.
