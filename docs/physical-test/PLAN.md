# Issue27 physical Windows test plan

User explicitly authorizes this PC and tester-owned isolated resources only. Baseline source19f306c plus reviewed pending safe-data-error fix; not full integrated acceptance. Main is untouched. Existing worktree reused on physical/master-windows-test, separate Draft PR.

## Scope and isolation

- Root C:/ProgramData/TesterPhysicalTest20261107 must be absent before initial exclusive creation. Persist one installation UUID/host fingerprint, verify before any later action, refuse unknown existing resources or reparse escapes.
- Private venv and immutable releases/<40hexSHA>; fixed API18765 and new PG55469 on127.0.0.1 only. Do not use existing postgresql-x64-18/5432, user credentials/databases or other services.
- Service names only TesterPhysicalTest20261107 and TesterPhysicalPg20261107. LocalService/service SID with RX on own code/config and Modify only own runtime/PGdata. No global ACL/security/firewall/network changes; never run a persistent elevated agent.
- New own PostgreSQL cluster/role/database, SCRAM credentials stored privately, app role nonsuperuser; B/E schemas migrated separately before service starts. No deletion of databases, backups, archives, logs or old releases. No credentials in GitHub/console.

## Execution and evidence

1. PREFLIGHT: read-only hardware/services/ports/permissions/git; actual SCM create access denied5, not elevated. Python3.14/PG18.4 available; chosen paths/ports unused. Existing DB inventory cannot authenticate; do not guess credentials.
2. SNAPSHOT: save exact source/tree, SHA256 artifact/manifest, redacted config and scope/ownership ledger; build dedicated test venv; independently review scoped native installer. Readiness matrix must expose missing download/derived/catalog/F/G/TI functionality.
3. INSTALL: only after payload/plan verified, invoke normal UAC for the already-authorized narrowly scoped installer. Register own PG/app services and dependencies; no other service/config/security modifications. SCM permission denial is ENVIRONMENT, not APP_CODE; do not loop unauthorized attempts.
4. START/TEST: prove real SCM process/account/path/dependency/status, loopback API and owned PG identity. Record ready/degraded truthfully. Source/SQL/core/terminal/strategy/control tests run only as implemented; missing component is NOT_IMPLEMENTED or NOT_INTEGRATED, no simulation of success.
5. DIAGNOSE/PATCH/RETEST: preserve logs, API responses, resource samples and screenshots; reproduce/fail regression first, fix on a separate branch/Draft; safe stop/drain/checkpoint, update owned immutable version, restart. Three failed repairs for same signature => BLOCKED and separate investigation, not endless restart.
6. ROLLBACK: stop own service and point only to prior verified owned release after compatibility/backup checks. Preserve current DB/data and artifacts; no destructive schema rollback. Backup/restore only own DB to a new owned DB, retain both.
7. ACCEPTED only with actual install/update/stop/crash recovery/data integrity, real scoped ten-instrument ingestion/analytics/API/UI and available F/G/TI tests, resource limits and exact-candidate CI. Optional source absence remains explicit. No real trading/main merge/other PC deployment.

Portable state: docs/physical-test/CHECKPOINT.json. Private machine state/ownership/secrets under the dedicated owned root and workspace work/physical-test. A service run is not proven by a Desktop automation; SCM and process/API evidence are separate. Existing15min MASTER automation was updated through official action for Issue27 and current-PC scope.
