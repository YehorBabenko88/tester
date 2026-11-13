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
