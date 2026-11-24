# MASTER #88 — Windows installer dependency inventory (non-deployable)

## Architecture decision

The sole new target is `C:\TesterLab`, with `app`, `config`, `data`,
`postgres-data`, `logs`, `scripts` and `install`. This is separate from
Preview v3.5. All service, task, shortcut and deletion targets must be listed
in a signed, pinned install manifest and independently re-attested at runtime.
`GUARDIANS_ENABLED=false`; no real trading or effectors.

## Legacy dependencies and conflicts

| Legacy entrypoint | Observed dependencies | Why not reuse as-is |
| --- | --- | --- |
| `scripts/windows/Install-Tester.ps1` | Python >=3.11, `requirements-platform.lock`, `src.api.bootstrap`, `scripts/windows/Start-Tester.ps1`, repo-local `.venv` and `runtime/config.json`; parameters database/role/port 8765 | Not a complete owned-service API/worker/dedicated PG installer; installs under repository runtime and refuses existing installation |
| `scripts/windows_preview/Install-TesterPreview-ADMINIT-v3.5.ps1` | PowerShell 5.1 admin, Git, py -3.12, PostgreSQL 18 binaries, scheduled tasks, local user, DPAPI, `%LOCALAPPDATA%\TesterPreview-ADMINIT`, `%ProgramData%\TesterPreview-ADMINIT-PG`, port 55473, web 18769, pinned legacy commit `dbf7316fd0f4b891f2d997b17ea26b71d5776500` | Distinct Preview lifecycle; backup/reinstall semantics and shortcuts differ; destructive cleanup cannot be transplanted into `C:\TesterLab` |
| `feature/adminit-preview-clean-install-v3-20261118` | User-pinned HEAD `0fec16eb19a2d42104af950b66d75805650fff65` | This is a historical preview source snapshot, not the unified MASTER installer candidate |

Do not delete or modify historical installers until independent dependency
search and CI prove no consumers remain. Treat Preview documentation and
acceptance evidence as historical. Never auto-migrate the old Preview
database or silently delete it.

## Unified installer phases

1. **Preflight** (read-only): exact 40-character commit and artifact digest,
   signature trust, Windows/Python/PG versions, service names/ImagePath,
   junction ancestry, free space, TCP listeners and PG5432 fingerprint.
2. **Stage**: only into versioned directories beneath `C:\TesterLab`;
   stage config and package under restrictive ACL, preserve existing
   installation on any failure.
3. **Dedicated PostgreSQL**: separate cluster data under
   `C:\TesterLab\postgres-data`, separate TESTER-only service identity,
   non-5432 port; refuse if service/port ownership differs.
4. **API and worker**: dedicated TESTER-only service registrations with
   recovery budgets, health probes, restart-on-boot, no autonomous trading,
   Guardians disabled.
5. **Terminal and shortcuts**: localhost terminal; exactly
   `TESTER — Запуск` and `TESTER — Удаление`, pointing to pinned
   signed scripts; exclusive install/start/uninstall lock.
6. **Destructive uninstall**: explicit `DELETE TESTER` plus second
   confirmation, verify all owned processes exited, delete only attested
   TESTER services, PG cluster, candles, results, logs, configs and shortcuts.
   No archive or backup retention. Any partial failure is reported; no
   broad process killing or arbitrary recursive deletion.
7. **Acceptance**: disposable Windows integration fixture, repeated install,
   start/stop/restart, boot recovery, deliberate fault injection, exact-head
   Python/Ruff and signed manifest checks, PG5432 unchanged, 24h soak.

## Current implementation boundary

`src/api/guardians/windows_install_gate.py` and
`src/api/guardians/windows_install_plan.py` are pure, side-effect-free
planning and rejection predicates. They do not register services, install
PostgreSQL, create shortcuts, delete data or execute Windows commands.
Actual Windows adapters and destructive effects remain **NOT IMPLEMENTED**.
Physical deployment is **NO_GO** until independent approval.
