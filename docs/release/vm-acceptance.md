# Windows VM acceptance — reproducible scenario

**Status: BLOCKED for agents.** The agent environment is a Linux container: no KVM or hypervisor, 2 CPUs, 7 GB RAM. It cannot run a Windows VM, and this session cannot see or use GitHub self-hosted runners. GitHub-hosted Windows runners cannot reboot and survive, so they cannot prove reboot, power-loss or shutdown behaviour. CI covers install, upgrade-free re-runs, SCM control, process kills and uninstall on a hosted runner (`test_full_native_installer_lifecycle`). Everything in this document is **additional** acceptance that only a real, disposable VM can provide. Nothing here has been executed.

The owner, or an operator the owner names, runs the scenario. Evidence goes into issue #88. Never run it on ADMINIT, on a machine with a production PostgreSQL on 5432 that matters, or with production keys in the VM.

## 1. Environment (reproducible)

- **Host:** Windows 11 Pro/Enterprise with Hyper-V, or any hypervisor that supports checkpoints and a hard power-off.
- **Guest:** a clean, fully patched evaluation image of Windows 11 Enterprise or Windows Server 2025, x64, at least 4 vCPU, 8 GB RAM, 80 GB disk. Generation 2, Secure Boot on, no domain join, a local administrator, and one standard user `tester-user`.
- **Network:** outbound HTTPS only, so the public Bybit endpoints are reachable. No inbound rules.
- **Checkpoint `C0-clean`:** taken right after OS setup and patching, before anything TESTER-related is copied in.
- **Package:** the exact release package built by CI from the candidate SHA (`release-candidate.yml`, job `package-contracts`).
  - Copy it in through a read-only ISO or VHDX.
  - Record the archive SHA-256 and the CI run id.
  - With a fixture (CI) key the result is *fixture acceptance* only. Production acceptance needs the owner-signed package (see `production-keys.md`).
- **Evidence folder:** `C:\Evidence` outside the install root, readable by Administrators.

PowerShell evidence helpers (run as Administrator; append each output to `C:\Evidence\<scenario>.txt`):

```powershell
Get-Service | Where-Object Name -like 'Tester*' | Format-List Name,Status,StartType
sc.exe qc <AppServiceName>; sc.exe qc <PgServiceName>; sc.exe qfailure <AppServiceName>
Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control' -Name PreshutdownOrder -ErrorAction SilentlyContinue
Get-WinEvent -FilterHashtable @{LogName='System'; Id=6005,6006,6008,1074,7036,7031,7034} -MaxEvents 200 | Format-Table TimeCreated,Id,Message -Wrap
Get-ChildItem C:\ProgramData -Filter 'TesterReleaseMaintenance_*' -Force; Get-ChildItem C:\ -Filter '.tester-lifecycle-*.lock' -Force
Get-NetTCPConnection -State Listen | Where-Object LocalPort -in 5432,55470
```

Restart-budget state comes from the owner command documented in `restart-policy.md` (`status --root <root>`). The PostgreSQL log is in the installation's `postgres-data\log`.

## 2. Scenarios and pass criteria

| ID | Scenario | Steps | PASS only if |
|---|---|---|---|
| V1 | Fresh install | From `C0-clean`, run `Setup-Tester.ps1` from the package (signed mode: `-ExecutionPolicy AllSigned` with the owner certificate trusted). Open the UI. Queue one small download. | Both services are AUTO_START and Running. PRESHUTDOWN timeouts are 60 s / 90 s. The UI works. The download completes. No listener on 5432 was created or touched. |
| V2b | Shutdown ordering evidence (PR #127) | After each V2/V3 reboot, read `postgres-data\service-lifecycle.json` and the restart-window status. Repeat V2 with a running download job and with the app still starting (reboot within 20 s of boot). | The PostgreSQL lifecycle phase before each stop is `dependent-stopped`, never `dependent-unknown` and never `dependent-stop-timeout` for an idle or ordinary app. Host crash count stays **0** after orderly reboots. A reboot during app start may cost at most one windowed crash; document the count. |
| V8b | C1 residual risk (PR #123) | From `C0-clean`, run Setup and hard power-off the VM immediately after the install root is created but before the first journal record (use a debugger breakpoint or a test build that pauses after `CreateDirectory`; record the method). Boot, re-run Setup. Then repeat with a standard user re-creating the root between delete and create (V8 variant). | Rollback never touches a root it did not record. The user sees the original refusal ("remove the empty folder…"), and no `TesterReleaseMaintenance_*` folder is left. If a TESTER-created empty root remains, the next Setup either replaces it (Administrators-owned, TESTER descriptor, exclusive open) or refuses with the remove-folder instruction; it is never adopted. After manual removal, Setup succeeds. |
| V2 | Routine reboots | 10× `Restart-Computer -Force`, at least 2 minutes apart, plus 3 reboots within 10 minutes. | The app starts after every boot. The restart-window status shows **0** host crash restarts. The PostgreSQL log has no "not properly shut down" after any orderly reboot. |
| V3 | Shutdown ordering | `shutdown /s /t 0` while a download job is running. Boot. | Same as V2. The interrupted job resumes or retries from its checkpoint. No data corruption findings. |
| V4 | Hard power loss when idle (crash budget) | Hypervisor hard power-off (`Stop-VM -TurnOff`) 3× within an hour, booting each time. | PostgreSQL crash recovery completes (it may take minutes and must not be killed). Each boot is counted as exactly 1 crash. A 4th hard power-off within the hour denies the start. `reset_restart_window` (owner) restores it. Kill-switch and owner-flag behaviour as documented. |
| V5 | Power loss during install | From `C0-clean`, run Setup; hard power-off at each phase: during dependency staging; after PostgreSQL registration; after `applying-runtime-acls`; during `sealing-ownership`; right after `ready`. Boot and re-run Setup after each. | Before commit, services are DEMAND_START, so nothing auto-starts on boot, and the re-run rolls back or completes cleanly. After commit, the re-run validates and switches to AUTO_START. In no case is uninstall impossible. No foreign file is touched. |
| V6 | Kill switch, then uninstall | Create `runtime\restart.disabled` exactly as `product-guide.md` describes. Stop and start the service. Remove the switch. Reboot. Uninstall. | No start while the switch exists. After it is removed, the next start is **not** counted as a crash. Uninstall succeeds. |
| V7 | Shortcut re-enrolment | Delete "TESTER — Удаление.lnk". Re-run Setup. Uninstall through that shortcut (elevated). | Uninstall succeeds. The root is fully removed. |
| V8 | Standard-user root attack | From `C0-clean`, as `tester-user`: create `C:\TesterLab` empty and hold a handle with WRITE_DAC (for example a PowerShell `[IO.File]`/`CreateFile` helper). Then, as admin, run Setup. Second variant: an admin-created folder that inherits Users write. | Setup refuses and tells the admin to remove the folder. No ACL is rewritten. No maintenance folder is left behind (C1). After the folder is removed, Setup succeeds. |
| V9 | Stale `postmaster.pid` | Installed. Hard power-off. Boot with the PostgreSQL service set to Disabled (owner test only). Start an unrelated process until a PID collides with the old `postmaster.pid` (or edit the PID in a copy to a live foreign PID). Uninstall. | Uninstall treats the pidfile as stale. The foreign process is alive and untouched. Removal completes. |
| V10 | Complete removal | Uninstall after V1/V2. | None of these remain: the root, the TESTER services or their registry keys, `TesterReleaseMaintenance_*`, `.tester-lifecycle-*.lock`, the shortcuts, `PreshutdownOrder` entries, or TESTER ACEs elsewhere. A pre-existing system PostgreSQL on 5432 (see V11) is untouched. |
| V11 | Coexistence | From `C0-clean`, install a standard PostgreSQL on 5432 with some data. Install TESTER, use it, uninstall it. | The 5432 service, its data directory and its ACLs are byte-identical before and after (hash the data directory with the service stopped). |
| V12 | Lab upgrade (optional) | A disposable copy of a `d6059af` lab install, never ADMINIT. Run `Update-TesterLabRelease.ps1 -SchemaUpgrade` with the owner-approved `schema-upgrade-manifest.md`. Inject one failure after commit, for example by killing the updater. | The schema upgrade commits with verified hashes. On injected failure the old app runs on the new schema. `restore` produces an owner-only database. `tester_lab_app` has no DELETE. |

Revert to `C0-clean` between independent scenarios unless a step says otherwise.

## 3. Evidence to publish in #88

For every scenario, publish:

- scenario ID;
- PASS or FAIL;
- candidate SHA;
- package SHA-256;
- the CI run that built the package;
- guest OS build;
- timestamps;
- the helper outputs above;
- relevant PostgreSQL log excerpts.

For a FAIL, add the exact step and a hypervisor screenshot. Partial runs are reported as partial. A scenario that was not run stays **NOT RUN**, never PASS.
