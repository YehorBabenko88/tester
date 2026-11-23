# ADMINIT TESTER desktop installer and full-uninstall contract (P0)

Status: SPECIFICATION ONLY; no installed binaries, live machine changes, or runtime permission. Owner: MASTER #88 / PR #89. Target: ADMINIT-JQ1IQSV, Windows. Default GUARDIANS_ENABLED=false.

## User-facing shortcuts

Create exactly two shortcuts on the interactive user's desktop after a verified, pinned installation:
- **TESTER — Запуск**: invokes a signed/pinned launcher with explicit install manifest; starts only TESTER-owned dedicated PostgreSQL service, then TESTER-owned API/worker service, waits for verified health, and opens localhost terminal. Fail closed on ownership/health mismatch; do not change the system PostgreSQL service.
- **TESTER — Удаление**: requests elevation and presents a clear destructive warning; requires the user to type the exact phrase `DELETE TESTER`. Stop only manifest-owned TESTER services/processes/tasks, remove their registrations and dedicated TESTER PostgreSQL cluster, delete all TESTER-owned application directories, downloaded candles/history, test runs/results, logs/configuration, and both desktop shortcuts. **No archive, backup, or data retention** is requested. A partial failure must be reported, never claimed as full deletion.

## Ownership and destructive safety

1. Installer writes a versioned `install.json` with absolute canonical install root, manifest schema, immutable package SHA-256, service names and executable paths, dedicated PostgreSQL data directory and port, owned Windows scheduled tasks, owned shortcut paths, and an explicit list of deletion targets. A manifest is not sufficient alone: re-attest service ImagePath, directory identity, database instance and ownership at deletion time.
2. Never delete or stop `postgresql-x64-18`, `C:\\Program Files\\PostgreSQL\\18\\data`, port 5432, any unrelated database, system Python, or user files outside manifest-owned roots. Dedicated TESTER PostgreSQL must have an independently verified cluster root and non-5432 port; never execute broad DROP DATABASE or recursive deletion against arbitrary paths.
3. Reject absent, malformed, ambiguous, tampered, symlink/reparse-point, junction, root-drive, Program Files, or out-of-scope paths. Do not follow reparse links while deleting. Refuse if the manifest or runtime service ownership cannot be verified.
4. Uninstaller must verify all owned services are stopped and their processes have exited before removing the dedicated cluster. Report failed operations and preserve evidence of failure outside the deletion root without retaining user history or secrets.
5. Display a second confirmation explaining that all TESTER candle history, test results and database contents are destroyed without backup. The user explicitly selected full cleanup. Ordinary deletion is not guaranteed forensic SSD sanitization.
6. Start and uninstall must never run simultaneously; require an exclusive local lock, per-operation receipt and idempotent recovery. Uninstall should handle stale, already absent TESTER services safely without touching other services.

## Observed ADMINIT preflight (user-provided 2026-11-19)

- `C:\\TesterLab` absent at last check; old stopped services `TesterLabApp_89189096be4c4cb3bf2b07b44f25fd60` and `TesterLabPG_89189096be4c4cb3bf2b07b44f25fd60` still registered. Do not assume either service is already removed.
- `postgresql-x64-18` running as NetworkService from `C:\\Program Files\\PostgreSQL\\18\\data`, listening on localhost port 5432. Preserve unchanged.
- No listener observed on ports 55432 or 18766 at the time of check. This is not proof that either port will remain free.

## Acceptance gates before installation

- Exact-head foundation and four Guardian PR CI green, including Python, Ruff, Windows platform acceptance, isolated negative/fault-injection tests. All PRs remain Draft/NO_GO until independently verified.
- Pin an approved installer commit/artifact hash and test on disposable Windows fixture with dummy data; confirm two shortcuts, start/stop/restart, localhost health, uninstaller full owned-data removal, repeated uninstall, tampered manifest refusal, foreign service protection, junction/reparse rejection, and rollback on partial failure.
- Physically inspect ADMINIT system PostgreSQL port 5432 and data before/after, verify no modification. Separate dedicated DB cluster; no trading, no autonomous Guardian effectors.
- Before first production uninstall, display destructive scope with actual paths and service names and obtain explicit typed confirmation; do not silently proceed.

Implementation owner: MASTER installer workstream. Code/Data/Storage/Exchange agents provide only owned acceptance contracts and tests; no duplicate installer implementation.
