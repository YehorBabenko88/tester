# Physical SCM payload contract — Issue27

MASTER is the sole physical operator. This branch supplies code and mocked tests;
E does not invoke installation, RunAs, SCM, ACL, database or firewall operations.
The earlier d28f492 financial request contract is retained as parent. No main merge.
The legacy TesterMarketLab host and plan-only scripts are unchanged.

## Fixed boundaries

- Root: `C:\ProgramData\TesterPhysicalTest20261107`.
- Service: `TesterPhysicalTest20261107`; demand start, own process, LocalService.
- Optional dependency ONLY `TesterPhysicalPg20261107`, explicitly selected at install/update.
- API `127.0.0.1:18765`; dedicated PostgreSQL `127.0.0.1:55469`.
- Private interpreter: root `venv\Scripts\python.exe`, pywin32 optional dependency inside venv.
- Config root `config.json`; runtime root `runtime`; immutable release root `releases\<40 lowercase hex SHA>`.
- Native CLI is `scripts/windows/physical_service.py`; there is no arbitrary command/shell argument,
  remove action, data cleanup, version deletion, global postinstall, ACL change or PG bootstrap.

ROOT must prepare the private venv using an existing service-readable Python,
install application dependencies plus `.[windows-service]` privately, and make
that venv immutable to LocalService. No pywin32 global postinstall is needed:
the SCM ImagePath directly invokes the private python and actual servicemanager
dispatcher, rather than LocatePythonServiceExe/HandleCommandLine helpers.

ROOT creates its cluster, database, B/E schemas and grants before service start.
No migration command is run by this installer/host. Existing supervisor performs
ordinary queue configuration DML and worker startup against prepared E schema.
Do not grant LocalService DDL/admin privileges merely to mask missing migrations.
No DSN/password belongs in a release or repository. ROOT separately protects
`admin-secrets.json` to owner/SYSTEM; E never reads it.

## Ownership marker and ACL prerequisite

ROOT creates `installation-owner.json` with exactly these keys:

```json
{"schema_version":1,"repository":"YehorBabenko88/tester","installation_id":"<canonical UUID>","host_fingerprint":"658ac47c38eb17a6","root":"C:\\ProgramData\\TesterPhysicalTest20261107","services":["TesterPhysicalTest20261107","TesterPhysicalPg20261107"],"created_at":"<UTC RFC3339>"}
```

Registry Parameters only under the exact app service: Owner=`TesterPhysicalTest20261107/v1`,
InstallationId matching marker, Root, Release, ManifestSHA256, ConfigPath.
Existing same-name service without ALL matching ownership/image/account markers
is rejected. Update requires STOPPED. Current ImagePath must equal the generated
fixed private-python/script command; a name alone never grants ownership.

MASTER must independently review/apply scoped ACLs before elevated execution:
owner/admin/SYSTEM control root/config/release/venv; LocalService read/execute
release+venv, read config+owner marker and modify runtime only. Root's separately
owned PG data permissions are MASTER's responsibility. E does not broaden ACLs.
Avoid writable ancestors, junctions, symlinks and hardlinks. Guards reject detected
reparse ancestors and hard-linked files; these checks are not an ACL/TOCTOU sandbox.
A protected immutable payload and exclusive MASTER operator are prerequisites.

## Configuration

Required explicit JSON fields: database_url, host, port, data_dir, frontend_dir.
Use host127.0.0.1/port18765/data_dir root/runtime/frontend_dir selected-release/frontend.
The DSN must explicitly use host127.0.0.1/port55469/dedicated database; no service or
hostaddr indirection. Physical preflight ignores environment configuration sources.
The host sanitizes inherited TESTER/PYTHON/PG variables before launching children.
Additional normal Settings fields remain validated; no default adapter is enabled.

Health now exposes `provider=unavailable|configured` and per-launch instance_id.
Configured means a provider object was configured, not proof of data completeness.
Default provider keeps overall health503/degraded even with a live worker. Missing
worker/DB/storage never qualifies for service operational readiness. Derived
requests still require explicit cutoff/step and their separate owned adapter.

## Release manifest and reviewed commands

Stage only application source/contracts/scripts/frontend (no .git, runtime, secrets,
venv, caches or node_modules) into the immutable release. `physical-manifest.json`:

```json
{"schema_version":"1.0","release_sha":"<same 40hex directory name>","files":{"src/api/physical_scm.py":"<SHA256>","...":"<SHA256>"}}
```

Include EVERY regular file except the manifest itself, using relative forward-slash
paths. Bounds:10000 files,64MiB/file,256MiB total,4MiB manifest. No extra/changed files.
ROOT computes the manifest SHA256 independently and records it in its approval ledger.
The approved digest is an integrity pin, not a signature. ROOT must verify all files
before executing the entry script elevated, since Python necessarily loads code
before it can check its own integrity. Do not run a mutable/unreviewed checkout elevated.

Commands below are for MASTER only, after its scope/UAC approval payload is ready.
They are concrete templates, not commands executed by E. Supply the exact approved
release SHA and separately reviewed digest; use a trusted elevated Python launcher
with a fixed argument list, not arbitrary command text.

```powershell
$physicalRoot = 'C:\ProgramData\TesterPhysicalTest20261107'
$release = Join-Path $physicalRoot 'releases\<reviewed40hexSHA>'
$python = Join-Path $physicalRoot 'venv\Scripts\python.exe'
$entry = Join-Path $release 'scripts\windows\physical_service.py'
$digest = '<approved-manifest-SHA256>'
& $python -I -B $entry install --release $release --manifest-sha256 $digest --with-postgres-service
& $python -I -B $entry start --release $release --manifest-sha256 $digest
& $python -I -B $entry status --release $release --manifest-sha256 $digest
& $python -I -B $entry stop --release $release --manifest-sha256 $digest
# For update: stop OLD registered release/digest, then update to NEW checked release/digest.
# update changes only owned SCM image/options/dependency; old versions/data are preserved.
```

Omit `--with-postgres-service` if ROOT keeps its own isolated PG foreground process.
The dependency switch never installs or modifies PostgreSQL service configuration.
Install/update/start return command submission evidence, not readiness/physical
acceptance. Stop submits control without claiming all descendants have stopped;
ROOT observes terminal SCM state, ports, process tree, logs and data preservation.
If registration is partially created before a native error, no rollback/delete is
attempted; missing markers fail closed and require MASTER inspection.

## Runtime, checkpoints, recovery and BLOCKED

SCM gets START_PENDING before bootstrap; real operational readiness has a60s bound
and advancing checkpoints. Only a health response with a random per-launch identity
AND database/worker/storage all ok can report RUNNING. Provider degradation is saved
honestly to runtime/scm-readiness.json; SCM RUNNING is process operational readiness,
not dataset readiness. Interrogate preserves current state. Source unavailable is
not a reason for an infinite start loop.

Supervisor is created suspended, assigned to a kill-on-close Windows Job Object,
then resumed. Graceful stop touches the owned runtime/stop.request; the supervisor
fences/drains queue work and awaits children. STOP_PENDING checkpoints continue for
up to30s; timeout kills the owned job tree, never unrelated processes. Checkpoints
are not job progress. Closing the job handle also fences descendants after crash.

The outer host re-raises failures to pywin32's native dispatcher so its final
STOPPED status remains an error; returning normally would overwrite it as success.
SCM recovery (ResetPeriod=INFINITE, no automatic counter reset) is exactly delayed restart15s, restart30s, then no action; non-crash
failure handling is explicitly enabled. No reboot or shell recovery command.
Runtime/scm-failures.json counts owned launches failing bootstrap/readiness/process
supervision, across manual starts. At3 it blocks before launching a child, with no
automatic reset/expiry or reset CLI. ROOT must stop after three same-signature
physical failures and report BLOCKED, not keep reinstalling/resetting the ledger.
If ownership cannot be proven, no ledger is written to unowned paths; ROOT's own
external action ledger enforces its attempt budget. Ledger edits/recovery require
a separate reviewed MASTER decision; preserve the original file as evidence.

## Evidence limits

E runs mocked native regressions only in this stage, plus lint/security/unit gates.
No actual SCM/RunAs/ACL/PG modification is performed by E. Actual service account,
DLL loading, SCM protocol, ACLs, database access, ports, stop and recovery remain
MASTER physical acceptance steps. Prior isolated PG evidence is from earlier heads,
not proof of this native host. No main merge, other service/PC/repository or trading.

Native API references: [pywin32 service framework](https://github.com/mhammond/pywin32/blob/main/win32/Lib/win32serviceutil.py),
[Microsoft job assignment](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject),
[Microsoft service configuration](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-changeserviceconfig2a).
