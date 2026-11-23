# Durable hierarchical restart policy

The canonical native release reserves every application service-host launch and
every API/worker launch before spawning. `DatabaseRestartBudget` is a reservation adapter,
not another executor. The existing SCM host and existing supervisor consume it.

The fixed per-installation lifetime limits are three host launches, six API
launches and six worker launches. Initial launches count. Reservations are
serialized by PostgreSQL row locking and synchronously committed before spawn; a crash after reservation
can waste a slot but cannot gain one. Existing supervisor five-per-minute retry
limits and SCM two failure-restart actions remain additional bounds. Reboot,
clock changes and parent replacement do not reset the lifetime counters.

The installer enrolls the installation UUID and exact source SHA in
`tester_platform.restart_budgets`. The owner controls this table. The application
login receives only EXECUTE on two fixed, schema-qualified SECURITY DEFINER
functions with `search_path=pg_catalog`; it cannot UPDATE, DELETE, TRUNCATE or reset
the authority. The separate PostgreSQL service writer boundary must prevent the
application SID from modifying the cluster. A local file rollback cannot reset
counters. Missing identity, unavailable database and failed commit deny launch.
Database/connection unavailability remains distinct from exhausted policy: the
service host reports an infrastructure failure so its existing two SCM recovery
actions can retry. Exhausted, disabled or malformed policy stops normally and
does not ask SCM to restart indefinitely. Neither case spawns an unreserved child.
There is no file-backed production fallback. The earlier mutable journal was
replaced after a valid-prefix rollback regression exposed its authority gap.

Creating `runtime/restart.disabled` prevents new reservations and asks the running
supervisor to drain and stop. Removing the file does not restore consumed slots.
For a durable owner-controlled kill switch, set the protected row's `disabled`
flag using the administrative recovery credential. The runtime login cannot clear
it. The supervisor checks authority at least once per second and drains on denial.
There is no automatic reset or silent repair. Budget exhaustion requires owner
diagnosis and an explicitly reviewed repair or clean reinstall. A normal graceful
stop/start consumes another host slot; repeated shortcut use while already
running does not create another host. Legacy foreground development without the
complete release identity retains its existing bounded process-local policy and
is not evidence for native release restart acceptance.

Regression tests cover concurrent real SQL reservations, client reconstruction,
identity/configuration mismatch, valid local-prefix rollback, denied SQL mutation,
owner kill switch and commit failure before spawn.
Real SCM recovery and a disposable VM reboot must separately prove the complete
native integration; SQL tests alone do not establish reboot acceptance.
