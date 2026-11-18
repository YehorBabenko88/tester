# ADMINIT Tester Preview v3: destructive reinstall and uninstall contract

Scope: only resources explicitly owned by **TesterPreview-ADMINIT**, never other PostgreSQL databases, Windows services, Codex accounts, or repositories.

## Fixed resource ownership

- Windows account: `TesterPgPreview` (unprivileged)
- Scheduled tasks: `TesterPreviewPg-ADMINIT`, transient `TesterPreviewInit-ADMINIT`
- App: `%LOCALAPPDATA%\\TesterPreview-ADMINIT`, web loopback `127.0.0.1:18769`
- Dedicated PostgreSQL 18 data: `%ProgramData%\\TesterPreview-ADMINIT-PG`, loopback `127.0.0.1:55473`
- Database: `tester_preview_catalog`; role: `tester_preview_app`
- Previous versions: `TesterPreview-ADMINIT.backup-YYYYMMDD-HHMMSS` under same LocalAppData parent, subject to verified ownership
- Two desktop shortcuts: `Tester Preview - Start.lnk`, `Tester Preview - Uninstall.lnk`

## Installer acceptance

1. Elevated Windows PowerShell 5.1; PostgreSQL 18 binaries, Git, Python 3.12 available.
2. `-Reinstall` stops only verified Preview PID, stops/unregisters only verified Preview tasks, removes only Preview-owned paths and backups, removes the dedicated account only when its description identifies Tester Preview, then provisions fresh resources. No automatic backup retention.
3. Refuse deletion on reparse points, unknown owner marker, unexpected task command, PID executable mismatch, or still-listening port 55473.
4. The PostgreSQL cluster is under ProgramData rather than an Administrator profile: the unprivileged task principal must traverse its data and initdb log paths.
5. `icacls` must grant the account SID using `*S-1-5-21-...` syntax, not an unprefixed SID.
6. Generate independent cryptographic passwords, SCRAM authentication, localhost-only PG. Protect app password using current-user DPAPI; do not log or commit passwords.
7. Explicit catalog migration is required; do not silently mutate any PostgreSQL 5432 database.
8. Uninstall asks for exact `DELETE TESTER PREVIEW` confirmation, removes all verified Preview-owned backups, task, account, PostgreSQL data and app files, and both shortcuts.
9. Never remove arbitrary directories, other user accounts, global PostgreSQL 18 service, Python installation, Git installation, or shared database clusters.
10. All UI/Bybit data remains unverified until actual catalog fetch. Release remains NO_GO.

## Observed failures fixed in v3

- v2: Windows `New-LocalUser -Description` exceeded the parameter limit.
- v2.1: `icacls` rejected bare SID; requires `*SID` syntax.
- v2.1: PostgreSQL account could not safely traverse Administrator-owned LocalAppData to reach its cluster; move private PG cluster to dedicated ProgramData path.
- v2.x: backups accumulated on reinstall and uninstall; v3 cleans only positively verified Preview backups.

This contract is for an isolated local Preview, not a production installer. Physical ADMINIT acceptance must confirm installation, two shortcuts, port isolation, restart, complete uninstall and no remaining Preview-owned artifacts.
