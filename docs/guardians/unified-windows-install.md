# Unified Windows lifecycle candidate

Canonical destination is `C:\TesterLab`. `python -B -m
src.api.unified_windows_install plan` prepares its layout, exact shortcut names
and disabled-Guardian policy without reading or changing that destination.
**Canonical deployment, native SCM installation, reboot recovery, signing and
physical desktop acceptance remain NO_GO / not validated.**

## Implemented disposable adapter

`UnifiedInstaller` requires separate fresh root/Desktop children beneath an
explicit fixture directory inside the OS temporary directory. It rejects the
canonical destination and refuses existing roots rather than adopting them.
It reuses `lab_install.plain` and `verify_tree`; there is no second Windows
service manager. The effectful backend uses the actual dedicated PostgreSQL
cluster and actual `src.api.supervisor`, which owns the API and worker children.
The intended native mapping is **two OS services, three logical roles**:
dedicated PostgreSQL plus the existing API/worker supervisor. Native deployment
would reuse `lab_scm.NativeSCM` after a separately reviewed adapter exists.

Before creating installation children on Windows, stage adds the current
individual creator's inheritable FullControl ACE to **only the explicit
temporary fixture parent**. PostgreSQL initdb drops Administrator group
privileges, so an Admin/System-only inherited DACL otherwise makes a fresh
cluster inaccessible. The existing `Set-TesterLabExplicitAcl -PreserveExisting`
helper preserves existing rules and uses SetFileSecurity without propagating
changes into existing children. No recursive ACL repair, owner change, system
ACL change or canonical-root access occurs. The helper is included in the
staged payload inventory; its PS5.1 child drops inherited `PSModulePath`.

The adapter stages an explicit SHA-256 file inventory and full source SHA.
The caller must preserve the independently approved inventory/source mapping;
the source SHA string alone does not prove Git provenance. `stage()` returns a
manifest digest. `initialize()` pins runtime executables, the actual base
Python executable, configuration and runtime metadata, and returns the updated
manifest digest. This caller-held digest is required on later invocations.
The disposable manifest is integrity pinned, **not digitally signed**. The
existing Ed25519 production release gates remain unchanged.

The cluster uses an explicit free non-5432 loopback port and UTF-8. Trust
authentication is used only in this disposable dummy-data fixture. The supplied
external Python/PG installation is a pinned dependency, not an owned deletion
target or a self-contained distributable. No system service is registered,
started, stopped or removed. `GUARDIANS_ENABLED=false` is forced in child
environments. No trading or Guardian effectors are enabled.

Readiness requires the installed supervisor module path, its actual child
listener, installation identity, and healthy database/worker/storage. HTTP503
from an unavailable external market provider is accepted as operationally
degraded; this is not market/provider acceptance. Start does not open a real
browser during the disposable test. Resource files and frontend are staged;
physical terminal/desktop interaction remains a separate gate.

Two actual COM shortcuts are created in the fake Desktop, with exact names
`TESTER — Запуск.lnk` and `TESTER — Удаление.lnk`. Targets, arguments, working
directory and pinned Python are read back and verified. The PowerShell launcher
runs outside the deletion root and preserves a source path for imports.
It never self-elevates: elevation and signed launcher behavior remain production
gates, not claims supported by this fixture.

Every operation takes an exclusive OS lock outside the deletion root. Startup
rollback only handles resources acquired by that call. Repeating start refuses
without stopping an existing installation. Stop reattests supervisor PID,
creation time, executable, fixed module and cwd; PostgreSQL stop reattests its
PID/executable/exact data-directory command line. Surviving processes under or
referencing the owned tree block deletion, including orphan API/worker children.
Only the current controller and exactly identified launcher ancestors are
excluded. Reparse descendants and changed root/cluster identities are refused.
This is a disposable local adapter, not protection against a concurrent
administrator racing arbitrary filesystem/process replacement.

Uninstall displays the destructive scope and requires typed `DELETE TESTER`
and second `YES`. It verifies shortcuts, stops owned processes, rechecks all
paths, then removes the owned tree (app, cluster, candles/history, results,
logs/configuration/scripts) and the two shortcuts. It creates no backup or
archive. A nonsecret successful-removal receipt remains beside the deleted
root, allowing repeated removal to report already absent. Failed operations
raise incomplete/refusal errors and never write a successful receipt; damaged
or partly removed metadata may require explicit independent recovery.

## Replay and evidence

```powershell
$env:TESTER_UNIFIED_WINDOWS_REAL='1'
# Optional when installed PostgreSQL binaries are elsewhere:
# $env:TESTER_UNIFIED_PG_BIN='C:\Program Files\PostgreSQL\18\bin'
.venv\Scripts\python.exe -m pytest tests/platform/test_unified_windows_install.py -q
```

The opt-in Windows test stages actual candidate source/resources with inventory,
proves the loaded supervisor is beneath installed `app`, initializes a fresh
cluster, creates/verifies real shortcuts, and exercises PowerShell start/stop
and typed uninstall. It checks repeat start/stop, cancellation, complete owned
deletion, repeated removal and foreign-sentinel preservation. Pure cases cover
manifest/source tampering, concurrent operations, orphan refusal and CLI
controller handling. Symlink testing skips when the account lacks privilege.

`.scratch/unified-evidence/windows-process-lifecycle.json` records the source
SHA, payload/manifest/config pins, installed module path, ports, logical roles,
readiness, deletion results and before/after read-only system PG5432
service/listener/config observations. It never connects to system PG5432.
Unreadable configuration files are explicitly recorded as unreadable, not
claimed verified. Record the candidate commit after publishing and rerun this
same fixture: a precommit run is not exact published-SHA acceptance.

The real Windows regression first constructs an Admin/System-only disposable
parent and proves raw initdb fails to create a cluster there. Stage then restores
creator inheritance before creating fresh children, and the full real lifecycle
must pass. The initial nonsecret failure is recorded in
`.scratch/unified-evidence/restricted-parent-initdb-before-fix.log`. A separate
actual-DACL test confirms existing protected child ACLs remain byte-identical.

These results do not establish native services, reboot recovery, 24-hour soak,
signer trust, administrator-race resistance, real user Desktop/UAC/browser,
or ADMINIT physical acceptance. The canonical target stays preparation-only.
