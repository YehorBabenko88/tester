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

## v3.1 task principal correction

Physical ADMINIT v3 failed at `Register-ScheduledTask` with HRESULT `0x80070534` (`UserId` name/SID mapping). The revised script `scripts/windows_preview/Install-TesterPreview-ADMINIT-v3.1.ps1` resolves `COMPUTERNAME\\TesterPgPreview` via `NTAccount.Translate(SecurityIdentifier)` and requires an exact match with `Get-LocalUser` SID before registering either task. It also checks task action ownership before unregistering the temporary init task and self-elevates the desktop uninstaller. This is a targeted correction, not yet a physical acceptance pass. If Windows still refuses a task logon, inspect local task logon rights and security policy; do not disable rights or weaken account isolation automatically.

## ADMINIT physical acceptance 2026-11-18

- v3.3 installed successfully; isolated PostgreSQL listens on 127.0.0.1:55473 and original 5432 remains unchanged.
- Browser API startup initially failed because `Get-Content -Raw | ConvertTo-SecureString` received a DPAPI ciphertext with trailing newline. Physical ADMINIT repair with `.Trim()` made `/api/capabilities` reachable; `market_catalog.enabled=true`, reason `configured`.
- v3.4 installer includes DPAPI write without trailing newline and read with `.Trim()`. It has NOT been physically reinstalled/tested.
- UI market screener is still empty. `sources.enabled=false` means no historical-data provider is bound; it does NOT by itself explain missing market instruments. The market catalog is a separate Bybit HTTP source, with persistent PostgreSQL snapshots and asynchronous refresh. Do not confuse these.
- Run `scripts/windows_preview/Test-TesterPreviewMarketCatalog.ps1 -RequestRefresh` from ADMINIT to inspect per-category snapshots and public Bybit connectivity. A successful queued refresh does not imply successful acquisition. A 403 from Bybit must be reported, not bypassed or replaced with synthetic market data.
- Do not claim real history, full source coverage, or live trading readiness. Release remains NO_GO.
