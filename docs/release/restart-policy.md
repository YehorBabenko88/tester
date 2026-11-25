# Durable crash-restart window

The canonical native release reserves every application service-host start and
every API/worker launch in PostgreSQL before spawning. `DatabaseRestartBudget` is a
reservation adapter, not another executor. The existing SCM host and existing
supervisor consume it.

## What is counted

Only crash restarts are counted, inside a fixed sliding window of one hour
(migration `008_restart_window.sql`):

| Role | Crash restarts admitted per hour | What counts as one |
|------|----------------------------------|--------------------|
| host | 3 | a host start whose previous host run never recorded a clean stop |
| api | 6 | a supervisor relaunch of an exited API child |
| worker | 6 | a supervisor relaunch of an exited worker child |

Each host start generates a random run token that stays in the service-host
process; only its SHA-256 digest is stored in the protected row. A requested stop
(operator stop/restart through the release controller, an SCM stop, or a Windows
shutdown/restart delivered as PRESHUTDOWN/SHUTDOWN) records a clean stop for exactly
that token before the supervisor drains. Therefore:

* a normal Windows reboot, OS auto-start and an operator start after a stop are
  clean starts: never counted and never refused by the window;
* a host kill, supervisor crash, readiness failure or power loss leaves the run
  unterminated, so the next start (SCM recovery, boot or operator) is counted once;
* the initial API/worker launch of each supervisor run is not counted
  (`api-start`/`worker-start` still require the identity row and both kill switches);
* when three host crash restarts already lie inside the window, a further crash
  restart is refused as exhausted policy until the oldest crash leaves the window,
  or the owner reviews and resets it. A retry of the same run after an ambiguous
  commit is idempotent.

Reservations are serialized by PostgreSQL row locking and synchronously committed
before spawn; a denied reservation rolls back, so the stored history never exceeds
the bound. A crash after reservation can waste a slot but cannot gain one. The
existing supervisor five-per-minute retry limit and the SCM two failure-restart
actions (24 hour reset period) remain additional bounds. Crash timestamps more than
one window in the future (wall clock moved backwards) are discarded rather than
blocking forever; a forward clock jump only expires history early.

Migration 003 is unchanged; its lifetime `*_starts` columns are retained only as
legacy evidence and are no longer incremented. Installations that exhausted the
old lifetime counter start again after the owner applies the platform migrations
(`src.api.db.migrate(owner_dsn)`, the existing upgrade path described in
security-signing.md). Fresh installations apply 008 during provisioning.

## Authority and security properties

The installer enrolls the installation UUID and exact source SHA in
`tester_platform.restart_budgets`. The owner controls this table and the crash
history is stored in that same row (no new table or sequence that a schema-wide
runtime grant could expose). The application login receives only EXECUTE on the two
fixed, schema-qualified SECURITY DEFINER functions `reserve_restart` and
`restart_counts` with `search_path=pg_catalog`; 008 replaces them in place so those
grants carry over. The App role cannot UPDATE, DELETE, TRUNCATE, reset or disable
the authority, cannot call the owner functions, and cannot end a host run without
its token. The separate PostgreSQL service writer boundary must prevent the
application SID from modifying the cluster. A local file rollback cannot reset
history. Missing identity, unavailable database and failed commit deny launch.

Database/connection unavailability remains distinct from exhausted policy. The
service host retries admission for up to 45 seconds while reporting START_PENDING
and then reports an infrastructure failure so its two SCM recovery actions can
retry. A running supervisor keeps its children for up to 120 seconds of
unavailable authority (a PostgreSQL restart) and retries a pending child restart
without spawning it; beyond that grace it drains and exits. Exhausted, disabled or
malformed policy stops normally and does not ask SCM to restart indefinitely.
Neither case spawns an unreserved child. There is no file-backed production fallback.

## Kill switches

Creating `runtime/restart.disabled` prevents new reservations and asks the running
supervisor to drain and stop. For a durable owner-controlled kill switch, set the
protected row's `disabled` flag using the administrative owner credential
(`private/database-owner.json`, Administrators/SYSTEM only). The runtime login
cannot clear it, and the owner reset below never clears it either. The supervisor
checks authority at least once per second and drains on denial.

## Owner diagnosis and reviewed reset

There is no automatic reset and no HTTP route. From an elevated prompt, with the
installed release (replace `<sha>` with the active source SHA):

```powershell
$Root='C:\TesterLab'; $Sha='<sha>'
$Boot="import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('src.api.restart_budget',run_name='__main__',alter_sys=True)"
& "$Root\dependencies\venv\Scripts\python.exe" -I -B -c $Boot "$Root\releases\$Sha" status --root $Root
& "$Root\dependencies\venv\Scripts\python.exe" -I -B -c $Boot "$Root\releases\$Sha" reset --root $Root
```

`status` calls the owner-only `tester_platform.restart_window_status(uuid)` (crash
timestamps, whether a host run is unterminated, kill switch, legacy counters);
`reset` calls `tester_platform.reset_restart_window(uuid)` after the cause of the
crashes has been reviewed. Both read the owner DSN from `private/database-owner.json`
and refuse a non-loopback, port 5432 or non-owner endpoint. The same functions can be
called with psql under the owner credential. Neither function is granted to the App
role; both are SECURITY INVOKER, so the App role's lack of table privileges would
also refuse them.

Legacy foreground development without the complete release identity retains its
existing bounded process-local policy and is not evidence for native release
restart acceptance.

## Evidence and remaining gates

`tests/platform/test_restart_window.py` runs against real PostgreSQL through a
nonowner login holding exactly the release grants: clean reboot cycles after an
exhausted legacy counter, windowed host/child crash restarts, aging out of the
window, forged clean stop, denied SQL mutation/reset, owner kill switch, and the
upgrade of a pre-008 database through `db.migrate`. Existing tests still cover client
reconstruction, identity/configuration mismatch, valid local-prefix rollback and
commit failure before spawn. Real SCM recovery, a real Windows reboot, OS shutdown
ordering against the PostgreSQL service and power loss must separately be proven on
a disposable VM; SQL and unit tests alone do not establish reboot acceptance.
