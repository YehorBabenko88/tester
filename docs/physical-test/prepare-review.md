# Physical prepare / journal bounded review

Reviewed 2026-11-07, read-only product review in `work/master-supervisor`.

No blocking defect found for the stated absent-root preparation on this PC. This is code/default-plan approval only, not evidence that ACL application, elevation, SCM, PostgreSQL or service operation succeeds.

Reviewed file SHA256:
- `scripts/windows/Prepare-PhysicalTest.ps1`: `0375A5DB1BB412D646B32C364E0CEAEE6A1866D8BB85654B90ACA2E297A49C6F`
- `src/integration/physical_journal.py`: `72DA1D9F92F617B5B3699CDF70A719F3A0856948557D0C9C3F5D4B0059FEE92C`

Preparation scope: fixed root only; default return at line 12 precedes mutation. Existing roots require matching repository/schema/installation/host/root/services marker (29–36). New root creation does not use Force; marker uses CreateNew. Each ACL application calls containment/reparse checks (54) before Set-Acl (63). Only the root and seven named directories are ACL targets; ProgramData parent is not modified. Private/state exclude LocalService and explicit Administrators grants; runtime/postgres-data/backups allow intended service modification. No SCM/PG commands, deletion, global policy changes or arbitrary caller-supplied root exist in this script.

Journal: same-directory exclusive temporary + flush/fsync + os.replace (44–48); exclusive cycle lock refuses interrupted/stale lock takeover (`supervisor_checkpoint.py` 53–68). Revision comparison before write prevents a loaded stale writer overwriting newer checkpoint (55–56). Foreign identity/root/schema fails before rewrite (36–42). Three failed repairs are persisted per reason signature and fourth same-signature repair is refused (58–63), including after restart. Observation does not consume repair budget, ACCEPTED cannot be set through this API.

Limits: reason identity is trusted caller input; investigation_required does not implement a global stop against all other reason signatures, and observations may change the displayed phase after BLOCKED. Treat the persisted failed_repairs map / investigation flag as authoritative; the journal is not an enforcement wrapper around physical repair execution. Containment checks are not race-proof directory handles against a hostile concurrent filesystem owner, and do not audit ProgramData ancestors. Assumptions are the stated trusted standard ProgramData, absent initial root, and no hostile concurrent owner. Atomic replacement is process-interruption protection, not a demonstrated power-loss durability guarantee. No live ACL/reparse attack or crash injection was performed.

Executed:
```
$env:TEMP=(Get-Location).Path; $env:TMP=$env:TEMP
../venv/Scripts/python.exe -m unittest -v test_physical_journal
& ./scripts/windows/Prepare-PhysicalTest.ps1 -InstallationId f23dab63-92c6-4e56-9027-4213c6b4dc60 -HostFingerprint 658ac47c38eb17a6
```
Four journal tests PASS, 0.061 s. Default plan reports exact fixed root/identities, ports 18765/55469 and physical_changes/deletes_data/changes_services/changes_global_security all false. Earlier run with sandbox default TEMP produced four filesystem PermissionError errors; redirected workspace TEMP run is the valid result. No -Create, SCM, PG, UAC or ProgramData modification executed by reviewer.
