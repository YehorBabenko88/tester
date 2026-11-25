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
legacy evidence and are no longer incremented or read by the reservation functions.

### Legacy (pre-008) code on an upgraded schema

Code released before 008 sends the untokenized role `host` for every service-host
start and `api`/`worker` for every child launch, including the initial ones. 008
keeps that code startable but bounded: each legacy `host` start is counted as one host
crash restart inside the same window (it can never record a clean stop), and it leaves
a run marker that no token matches, so the next tokenized start after it is counted
as well. Legacy `api`/`worker` launches are counted in the child window. A legacy
application therefore starts at most three times per hour and never unboundedly.

### Exhausted pre-008 installations: reinstall is the supported recovery

The protected row is bound to the installation UUID **and the exact source SHA**
enrolled at install time (`release_database.provision_release_databases`, from the
installation marker). `reserve_restart` and `restart_counts` deny any other source
SHA, and no function rebinds the row (`reset_restart_window` clears crash history
only, never `source_sha` or `disabled`). Code of a different release can therefore
never use an existing row, and an in-place release upgrade is not certified
([product-guide.md](product-guide.md)). An installation that exhausted the pre-008
lifetime counter (fourth host start refused) is recovered by a clean reinstall of a
signed release that contains 008 (uninstall, then Setup), which enrols a fresh row
in new databases; data carried across a reinstall is not certified.
Applying the platform migrations to that installation's database does **not** make a
newer release usable on it. It only lets the installed pre-008 release itself start
again under the bounded legacy rule above; this uncertified owner action is not an
upgrade path. Once 008 is applied, the owner may clear the window after reviewing
the crashes with the owner credential and psql (the installed pre-008 release has no
`reset` command): `SELECT tester_platform.reset_restart_window('<installation-id>');`.
Fresh installations apply 008 during provisioning.

The legacy portable lab installation (`scripts/lab_portable.py`, updated by
`Update-TesterLabRelease.ps1`) enrols no `restart_budgets` row and its service host
does not reserve restarts, so the lab
`OLD_APP_RESTORED_ON_UPGRADED_SCHEMA` rollback is not gated by this table.

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
supervisor to drain and stop. Both kill switches deny every start and reservation but
still accept the clean-stop record of the exact current host-run token
(`host-stop:<token>`): stopping the service while a switch is set therefore does not
turn the first start after the switch is removed into a counted crash restart. Create it only with the supported elevated procedure in
[product-guide.md](product-guide.md) (`New-Item`, owner set to Administrators with
`icacls /setowner`, folder permissions unchanged); uninstall accepts exactly that file
and removes it with the installation. For a durable owner-controlled kill switch, set the
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
window, forged clean stop, denied SQL mutation/reset, owner kill switch (clean stop
still recorded under both switches), the upgrade of a pre-008 database through
`db.migrate`, and legacy untokenized `host`/`api`/`worker` reservations on that
upgraded database (bounded by the window). Existing tests still cover client
reconstruction, identity/configuration mismatch, valid local-prefix rollback and
commit failure before spawn. Real SCM recovery, a real Windows reboot, OS shutdown
ordering against the PostgreSQL service and power loss must separately be proven on
a disposable VM; SQL and unit tests alone do not establish reboot acceptance.
