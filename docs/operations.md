# Local operation and Windows installation

## Review revision: admission, drain and SCM planning

The database control row serializes create/retry/resume/claim admission. Default
limits are 256 pending (queued/running/paused) and 2 running jobs. Explicit stricter
caller budgets are persisted so another API instance cannot bypass them. A supervisor
startup applies configured limits. Capacity exhaustion returns 429; drain returns 503;
both include `Retry-After: 5`. `python -m src.api.control drain` stops admission and
claims across instances atomically; `resume` reopens them. Stop-Tester drains before
writing stop.request. Workers check local stop before claim and release a just-claimed
lease if stop raced the database round trip. Active work remains checkpoint-fenced.

JsonFormatter now allows fixed event IDs only, never arbitrary message/exception text
or args. Correlation is typed UUID job_id/run_id and 32-hex trace_id; numeric disk
measurements and closed component/error enums are admitted. Invalid extras are dropped
or replaced with `Error`. Logs remain separate rotating component files.

`python scripts/runtime_preflight.py` checks socketpair/eventloop in a subprocess with
a 5s deadline before ASGI testing. The earlier review sandbox blocked in stdlib
socket.accept before application code. Root full-access Python 3.14 and 3.11 probes,
followed by bounded real TestClient tests, passed. A failed preflight is a failed HTTP
gate; no skipped/hung endpoint test is counted as passed.

`Service-Tester.ps1 -Action install|repair|uninstall` only prints a JSON DRY_RUN plan.
There is no execute switch. The proposed native SCM entrypoint calls pywin32
PrepareToHostSingle/StartServiceCtrlDispatcher and hosts ServiceFramework; it is
distinct from the existing foreground supervisor. Plans use the dedicated virtual
account NT SERVICE\TesterMarketLab, delayed start, three bounded restarts, explicit
runtime/venv/base-Python read/execute ACL review and runtime modification rights.
Uninstall plans retain the database, role and runtime. The plan never mutates SCM,
registry, ACL or service state. Native host code requires the windows-service extra;
it was NOT installed or run here. Upstream API reference:
[pywin32 service utilities](https://github.com/mhammond/pywin32/blob/main/win32/Lib/win32serviceutil.py).

Physical service installation remains forbidden. Disposable-VM native start/stop,
virtual-account access, DLL resolution, process-kill and reboot tests remain gates.
Hard OS CPU/RSS enforcement, real-provider concurrency/network shaping and fairness
are not established by this revision. The synthetic 10-symbol benchmark in
docs/evidence/ten-symbol-synthetic.json measures bounded local streaming/queue depth,
not exchange throughput; total network bytes are unmeasured (null).

Requirements: Python 3.11+, PostgreSQL 14+ binaries/server, PowerShell 5.1+ or 7,
at least 1 GiB free disk (configurable) and internet for dependency installation.
No trading API keys. Dependencies are constrained by `requirements-platform.lock`.
The bootstrap creates a dedicated database and login with no superuser, role-creation
or database-creation privilege. It never adopts an existing database or role.

## Install (after isolated acceptance)

Clone `workstream/e-platform` into a writable `tester` folder. Run:

```powershell
./scripts/windows/Install-Tester.ps1
```

The installer checks Python and disk, protects `runtime` with a fresh user/SYSTEM-only
DACL, creates `.venv`, installs dependencies, then prompts for a PostgreSQL admin DSN
using hidden input. Example shape: `host=127.0.0.1 port=5432 dbname=postgres user=postgres password=...`.
Do not put the actual DSN in command history or repository files. Automation may
provide `TESTER_ADMIN_DSN` in the environment for the setup subprocess only; clear it
immediately afterwards. The generated application password is written only to
ACL-protected, ignored `runtime/config.json`; it is not logged or committed.
The administrator credential is not persisted.

Use `-Database tester_myapp -Role tester_myapp -Port 8765` to choose dedicated names.
Names must start `tester_`, followed by lowercase letters/digits/underscores.
Use `-NoStart` to provision without starting any application processes.
PostgreSQL connectivity/version and role/database existence are validated before
creating resources; options are validated before resource creation. Missing
PostgreSQL is an explicit setup failure, never an automatic server install or
modification of existing databases. A successful install opens the browser only
after API, database, storage and worker health are good.

## Start and stop

```powershell
./scripts/windows/Start-Tester.ps1
./scripts/windows/Stop-Tester.ps1
```

Start uses a hidden supervisor; `-NoBrowser` suppresses browser opening. The API
binds to `127.0.0.1:8765`; `/docs` exposes API documentation. Frontend is served
from `frontend/` when installed. This branch's fallback landing page links API docs;
the full terminal is supplied by D. Configure the A/B/C adapter factories in protected
config before expecting market data. Missing adapters return explicit 503; submitting
a job without an ingestion adapter results in `failed/provider_unavailable`.

Stop creates `runtime/stop.request`. API drains requests; worker cooperatively
releases the current job with its committed checkpoint. Supervisor waits 20s, then
terminates only its own unresponsive children. Interrupted leases recover on expiry.
Do not kill arbitrary Python/PostgreSQL processes. A file lock prevents duplicate
supervisors. No Windows service or scheduled task is installed; automatic restart
is scoped to the running supervisor. An OS reboot requires running Start again.

For a foreground session:

```powershell
$env:TESTER_CONFIG_FILE = (Resolve-Path runtime/config.json).Path
.venv/Scripts/python -m src.api.supervisor
```

Without a config file, `TESTER_DATABASE_URL`, `TESTER_DATA_DIR`, `TESTER_PORT`,
`TESTER_MIN_FREE_BYTES`, `TESTER_STORAGE_QUOTA_BYTES`, `TESTER_LEASE_SECONDS`,
`TESTER_PROVIDER_FACTORY`, `TESTER_HANDLER_FACTORY`, `TESTER_FRONTEND_DIR` are read.
Config-file values take precedence over these environment defaults. Host validation
permits loopback only. Defaults: 1 GiB minimum free, 100 GiB application quota.

## Diagnostics and recovery

Read `/api/health`: 200 only if platform migrations are readable, worker heartbeat is
under 30s old and storage is healthy. A provider can still be unavailable: health
checks infrastructure, not A/B/C/D feature readiness. Structured rotating logs are in
`runtime/logs/{api,worker,supervisor}.jsonl` (5 MB each, five files per component).
Jobs expose sanitized error codes, progress, attempts and last committed checkpoint.
The supervisor restarts a failed child up to five times per minute with backoff;
on the next failure it stops the application and exits nonzero.

Low disk/quota blocks new jobs (507), suspends claiming new tasks and emits disk_alert
logs. Adapters must check quota at chunk boundaries because an already running
provider may consume more space. Storage monitoring scans only the configured root
without following links. Never delete market files to clear an alert without checking
B's provenance/recovery instructions. Platform does not automatically delete archives.
`cleanup_temp(root)` removes only old direct `*.part` entries in the platform-owned
`tmp/` staging directory, skips links/junctions and never recurses. Ingestion adapters
must use their own downloads directory; never put active files in this cleanup area.

If setup fails after saving config, preserve it and inspect the failure. Retry platform
migrations with the app DSN via the command below. If database creation succeeded but
the config could not be written (disk/ACL failure), use a PostgreSQL administrator to
reset only the dedicated application's role password, write a protected config, then
retry migration. The installer never removes a database to fix a partial installation.
If only a role was created and CREATE DATABASE failed, bootstrap removes only that
new unused role. Existing resources are never dropped. A checksum mismatch on an
already applied migration is a hard failure: restore the original migration and add a
new migration instead of changing history. Migrations run in one transaction under
an advisory lock. No destructive automatic downgrade is provided.

```powershell
$env:TESTER_CONFIG_FILE = (Resolve-Path runtime/config.json).Path
.venv/Scripts/python -c "from src.api.config import load_settings; from src.api.db import migrate; migrate(load_settings().dsn)"
```

## Update

Stop, back up, review the target commit and release notes, fetch/checkout that commit,
install `.venv/Scripts/python -m pip install -e . -c requirements-platform.lock`, run
migrations above, run acceptance in a disposable environment, then Start. Preserve
`runtime/config.json`. Do not rerun first-install bootstrap against an existing DB.
Rollback code only if its schema compatibility is documented; otherwise restore the
backup into a new dedicated database and point the protected config to it.

## Backup and restore

Stop workers before a coordinated backup. Use `pg_dump --format=custom` with a
dedicated-role connection supplied via protected libpq passfile or interactive prompt,
not a password on the command line. Capture the whole dedicated application database,
including tester_platform, B's schema and migrations. Copy immutable external archives
and a manifest of hashes at the same checkpoint. Store the encrypted configuration
backup separately with restrictive access. Backups contain sensitive connection data
and are not repository artifacts.

Restore with `pg_restore --no-owner --no-privileges` into a NEW dedicated database
owned by a newly provisioned application role, never over an unrelated database.
Verify manifest hashes, apply compatible forward migrations, and run coverage/health
and a disposable API acceptance before changing production configuration. Retention
and off-device backup policy are operator decisions; this branch does not delete backups.

## Acceptance boundaries

`python scripts/acceptance.py --integration --security` is the single platform gate.
It requires dev dependencies and a disposable `TEST_DATABASE_URL`; tests truncate
the platform jobs table and create fixtures, so NEVER use a valuable application DB.
It runs lint/format, OpenAPI drift, all repo tests, real HTTP/DB/worker startup/shutdown,
Bandit medium/high checks and pip-audit. Synthetic fixtures are labeled and no network
market data is claimed by these tests.

On a disposable Windows CI runner/VM only:

```powershell
./scripts/windows/Test-Isolated.ps1
```

This creates a new PostgreSQL cluster under `.scratch`, tests first installation,
ACL hardening, migrations, API/worker readiness, start/stop and security gates, and
stops that cluster in finally. Cluster files are retained for diagnostics, never
recursively deleted by the script. CI also runs PostgreSQL 16 and Python 3.11/3.14 on
Linux. Full provider/market download acceptance, D terminal acceptance and physical
Windows deployment remain separate gates. Draft PR must not be merged/deployed
before those gates and review.
