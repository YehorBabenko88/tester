# Windows native restart protocol — opt-in pre-integration proof

The existing PC4 runtime is **not changed or enabled**. This source-only
module models a birth-bound, externally witnessed native stop/restart gate.

## Safety invariant

Never infer process exit from PID disappearance, a user-written marker, a
listener, or a self-reported application status. The controller must enroll
**both** API and worker with externally held OS process handles, record OS
birth time, executable, canonical working directory, exact argv, Windows
username, generation marker and immutable configuration hash, then re-observe
both processes immediately before creating any stop request.

The caller supplies a separately validated, generation-scoped cooperative
stop callback; the native witness module does **not** signal or kill anything
on its own. It waits on the *same original handles*, requires exit code 0 from
both, then writes a create-only closure receipt in an owner-controlled root.
A missing handle, stale birth, foreign user, different image/argv/config,
changed owner marker, timeout, nonzero exit or missing closure is a hard
refusal, preserving all records.

No terminate/kill, SCM, privilege elevation, UAC, reboot, PG mutation or
native PC4 installation is performed.

## Implemented now

- `scripts/ops_native_windows_witness.py`: strict immutable OS identity
  model; injected OS re-observer; paired process-handle validation;
  cooperative-stop callback interface; bounded handle waits; create-only
  seal and refusal to authorize restart on failed receipt.
- `tests/platform/test_ops_native_windows_witness.py`: pure regressions
  with deterministic process-handle fakes. They do **not** claim Windows
  native process E2E or physical PC4 support.
- Isolated OPS workflow runs these tests under Python 3.12/Linux.

## Windows activation blockers

**BLOCKED:** actual Win32 handle acquisition from spawned API + worker,
cryptographically bound append-only manifest chain across restarted
controller, generation-scoped stop integration inside
`src.integration.pc4_runtime` and `src.api.worker`, PG incarnation
verification, exact Windows process lifecycle E2E and data-preserving
recovery. In particular, the existing installed PC4 source uses fixed
`runtime/stop.request` and rejects retained process records. Do not
remove those files or retroactively enroll a running installation.

A production candidate must use an independent immutable enrolment before
first start. A user/operator must explicitly authorize any future PC4
physical action. Until those gates have real evidence, **native release NO_GO**.

## Existing independent evidence

Prior exact-head `50e024138152bf72c6354b451fbb5fab6ff640f7`
OPS CI [run 43479008623](https://github.com/YehorBabenko88/tester/actions/runs/43479008623)
had 69/69 pure and 7/7 real isolated PostgreSQL 16 tests PASS, including
two-process disposable restart, checkpoint/lease fencing, rollback, WAL/queue
resource measurements, and bounded 50-job ten-symbol PG queue contention.
These facts **do not** imply Windows native restart PASS.

Separate issue: other repository workflows on that source had mixed CI
failures, so this PR should remain Draft and must not merge into main.
