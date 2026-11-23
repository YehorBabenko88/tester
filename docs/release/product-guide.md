# Installation and operation

Supported release target: Windows x64 with a complete private CPython 3.14.10 runtime, native pywin32 and dedicated PostgreSQL binaries. The CI fixture uses Windows Server 2022; Windows 11 physical lifecycle acceptance is a separate outstanding gate. Linux supports runtime/development checks; the Windows installer and SCM shortcuts are not Linux features.

Obtain the release package and independently obtain the expected source SHA, release manifest SHA-256 and trusted production public-key SHA-256 from the owner. Verify the distribution before running its installer. From elevated PowerShell in a disposable accepted target environment:

```powershell
.\Setup-Tester.ps1 -ManifestSHA256 '<independent-envelope-sha256>' -TrustedPublicKeySHA256 '<owner-pinned-public-key-sha256>' -ExpectedSourceSHA '<exact-40-character-source-sha>'
```

The canonical destination is `C:\TesterLab`. Do not install over the existing ADMINIT installation without owner authorization. Setup performs preflight, verifies the signed complete inventory, creates the private virtual environment, installs hash-locked offline wheels, initializes the owned PostgreSQL cluster, applies migrations, registers native services and checks readiness. Preserve the installation state/log on failure and rerun only after ownership and recovery state are understood.

Two desktop shortcuts are intended: **TESTER — Запуск** starts owned services and opens the ready terminal; **TESTER — Удаление** invokes the owned uninstaller. Repeated launch must retain a single service process. Service lifecycle helpers live in the installed release's `scripts/windows` directory. Read the installed helper's PowerShell parameters rather than inventing service names; names and paths are installation-UUID-bound.

Removal displays the owned resources and requires exact `DELETE TESTER` followed by `YES`. It irreversibly removes the owned cluster, results, market history, configuration, logs and shortcuts. Unknown ownership, altered service identity, links or foreign contents must cause refusal. There is no retained uninstall backup. Exercise removal only on a dedicated temporary installation during acceptance.

For diagnosis preserve source SHA, envelope digest, installation UUID, service status, installation journal and API/worker readiness. Keep credentials and database passwords out of published evidence. Recovery must verify owned service executable and cluster identity before restarting. System PostgreSQL port 5432 and unrelated files must remain unchanged.

Updates require a newly signed source-bound package and data-preservation acceptance; a generic automatic in-place upgrade is not yet certified. Keep production use blocked until the Windows install → recovery → reboot → data verification → uninstall → clean reinstall matrix passes. Never treat process-backend or dry-run evidence as native SCM acceptance.
