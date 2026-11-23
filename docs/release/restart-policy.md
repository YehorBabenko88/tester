# Durable hierarchical restart policy

The canonical native release reserves every application service-host launch and
every API/worker launch before spawning. `RestartBudget` is a reservation journal,
not another executor. The existing SCM host and existing supervisor consume it.

The fixed per-installation lifetime limits are three host launches, six API
launches and six worker launches. Initial launches count. Reservations are
serialized across processes and fsynced before spawn; a crash after reservation
can waste a slot but cannot gain one. Existing supervisor five-per-minute retry
limits and SCM two failure-restart actions remain additional bounds. Reboot,
clock changes and parent replacement do not reset the lifetime journal.

The installer exclusively initializes `runtime/restart-budget.jsonl` and its lock
before ownership sealing. Identity binds the installation UUID, exact source SHA
and policy. Runtime requires both files; missing, corrupt, truncated, hardlinked,
reparse or wrong-identity state denies launches. A partial append also denies
further launches. The OS-protected runtime writer boundary is required; this is
not a defence against a compromised administrator or the authorized service
deliberately replacing its own journal.

Creating `runtime/restart.disabled` prevents new reservations and asks the running
supervisor to drain and stop. Removing the file does not restore consumed slots.
There is no automatic reset or silent repair. Budget exhaustion requires owner
diagnosis and an explicitly reviewed repair or clean reinstall. A normal graceful
stop/start consumes another host slot; repeated shortcut use while already
running does not create another host. Legacy foreground development without the
complete release identity retains its existing bounded process-local policy and
is not evidence for native release restart acceptance.

Regression tests cover independent processes, concurrency, parent reconstruction,
identity mismatch, missing/corrupt state, kill switch and denial before spawn.
Real SCM recovery and a disposable VM reboot must separately prove the complete
native integration; journal tests alone do not establish reboot acceptance.
