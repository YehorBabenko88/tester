# TESTER — independent ADMINIT Windows release acceptance protocol (2026-11-23)

**Status: SPECIFICATION / NOT RUN / RELEASE NO_GO.** This document is not
permission to deploy, stop a service, delete data, or issue a GO. Current
foundation is pinned to `bd619eb88e9444c3b414739a27f3053fb240251e`
(10/10 exact-head GitHub Actions PASS); combined Guardian and installer
acceptance is separate and unproven.

## Hard preconditions

- A separate reviewed, immutable combined release-candidate SHA descended
  from approved foundation; all four Guardian exact-head 8/8 CI and combined
  Windows/Linux Python, Ruff, negative/fault and integration CI PASS.
- Signed Ed25519 installer manifest and independently approved signing-key
  fingerprint, SHA-256 of actual packaged executables, full 40-character
  source SHA; no self-asserted trusted key.
- Canonical install root **only** `C:\TesterLab`; dedicated PostgreSQL data
  beneath it, independently attested instance/service/port, never the
  system `postgresql-x64-18` on 5432. Exactly one API service and one worker
  service. Guardian effects remain **disabled**.
- A **disposable Windows VM**, snapshot of unrelated system state, dummy
  candles, dummy test results and dummy database. Testing full deletion on a
  real ADMINIT installation or on the system PostgreSQL is forbidden.
- A named independent tester who did not implement installer or uninstaller;
  attest the actual console, physical mouse and GUI behavior.

## Read-only preflight on the future target

The reviewer may execute **read-only** PowerShell commands after selecting
the correct Windows host; they do not install, elevate, stop or delete:

```powershell
$root = 'C:\TesterLab'
Get-CimInstance Win32_Service -Filter "Name='postgresql-x64-18'" |
    Select-Object Name, State, StartName, PathName, ProcessId
Get-NetTCPConnection -LocalPort 5432 -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalAddress, LocalPort, OwningProcess
Get-CimInstance Win32_Service |
    Where-Object { $_.Name -like 'Tester*' } |
    Select-Object Name, State, StartName, PathName, ProcessId
Get-ChildItem -LiteralPath $root -Force -ErrorAction SilentlyContinue |
    Select-Object FullName, Attributes
```

Before/after evidence MUST record exact system PostgreSQL service identity,
ImagePath, PID/service relationship, listener, cluster path, and read-only
configuration fingerprint; no command may attempt to stop/restart/modify it.

## Disposable installation and lifecycle test matrix

Each row is initially **NOT RUN** and must be individually signed by an
independent reviewer, with full release SHA, artifact SHA-256, Windows build,
PowerShell version, test host, timestamp, sanitized logs and actual outcome.

| Case | Independent acceptance criteria | Current |
| --- | --- | --- |
| Manifest | Verified approved signer, exact source/artifact SHA, all ownership paths and declared service/task names | NOT RUN |
| Clean install | Own API, worker and dedicated PostgreSQL only, under C:\TesterLab, non-5432 port, no foreign service mutation | NOT RUN |
| Reinstall | Reinstall same SHA is idempotent or explicit fail-closed; newer SHA may only upgrade via approved manifest | NOT RUN |
| Start shortcut | Exactly `TESTER — Запуск.lnk`, canonical signed launcher, owned PG then API/worker, verified localhost terminal opens | NOT RUN |
| Stop/restart | Bounded drain, service stop, process exit, restart order and health; no accidental second instance | NOT RUN |
| Windows reboot | Own services recover on next boot, bounded retries, no privileged third-party changes | NOT RUN |
| Four Guardians | Code/Data/Storage/Exchange import and health checks pass, `GUARDIANS_ENABLED=false`; no trade or effects | NOT RUN |
| Uninstall shortcut | Exactly `TESTER — Удаление.lnk`, elevation prompt and typed `DELETE TESTER` with *second* destructive confirmation | NOT RUN |
| Negative confirmation | Cancel/incorrect phrase must preserve all TESTER files/services, no arbitrary deletion | NOT RUN |
| Complete deletion | After owned process exit, remove own services, tasks, cluster, candles/history/raw data, queue/checkpoints, results, logs, config, scripts, both shortcuts; no archives or backups retained | NOT RUN |
| Idempotent deletion | Second invocation reports already absent without touching foreign resources | NOT RUN |
| Tampered inputs | Refuse modified signature/digest, service ImagePath, different PG cluster, junction/reparse, symlink, ADS/device paths | NOT RUN |
| Partial failure | Simulate failed stop, locked file, interrupted removal; explicit incomplete receipt without storing user data or secrets | NOT RUN |
| System PG invariant | `postgresql-x64-18`, cluster and port 5432 show identical independent fingerprints before and after every case | NOT RUN |
| 24h soak | Observe bounded resource use and recovery, no shell/trading/unauthorized Guardian effector | NOT RUN |

The two Cyrillic desktop shortcuts previously passed a **disposable GitHub
Actions shortcut fixture**; that test is NOT a production signed installer
or real user-desktop acceptance.

## Explicit implementation gaps before any physical GO

- `windows_install_gate.py` verifies plans/attestations, not actual Windows
  SCM or filesystem identities; the `signed_bundle_verified` flag alone is
  insufficient unless tied to the independently pinned signature verifier.
- `windows_release_lifecycle.py` is a pure ordered plan; no effectful
  installer, signer-verified service installer or uninstall adapter exists.
  The legacy `Manage-TesterLab.ps1` targets a different marker/release
  structure and is not automatically compatible with this new contract.
- A trusted versioned `tester_lab` runtime identity and independent
  after-check are still missing. `tester_preview` policy must not be
  silently rebranded or bypassed.
- Transport HTTP 403 retry behavior and broad Bybit-host endpoint admission
  differ from Exchange's proposal-only fail-closed policy; tests of the
  actual network boundary remain required.
- Historical Preview installers and PR #94 candidate branches remain
  untouched until their dependencies and deployment consumers are audited.

**Gate:** independent reviewer records exact PASS for all critical rows,
signed package provenance, 8/8 CI for every Guardian and combined candidate,
and the immutable system PG5432 invariant. Only then may MASTER request
human RELEASE GO and provide effectful ADMINIT deployment commands.

Until then: **RELEASE NO_GO, no deployment, no deletion, no main merge**.
