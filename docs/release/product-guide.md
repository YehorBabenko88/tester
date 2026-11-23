# Installation and operation

Supported release target: Windows x64 with a complete private CPython 3.14.10 runtime, native pywin32 and dedicated PostgreSQL binaries. The CI fixture uses Windows Server 2022; Windows 11 physical lifecycle acceptance is a separate outstanding gate. Linux supports runtime/development checks; the Windows installer and SCM shortcuts are not Linux features.

Obtain the release package and independently obtain its ZIP, Setup-script, envelope, Ed25519-key and raw code-signing-certificate SHA-256 pins, together with the exact source SHA and current owner trust policy. These are two separate trust mechanisms: Authenticode/AllSigned governs PowerShell execution; Ed25519 authenticates the complete payload inventory. An Ed25519-valid bundle does not make an unsigned script acceptable to AllSigned.

Follow the protected-staging and trusted-console bootstrap in [security and signing](security-signing.md) before executing any package helper. Only after that procedure verifies the protected Setup bytes and independently pinned publisher may an elevated trusted owner console invoke:

```powershell
powershell.exe -NoProfile -ExecutionPolicy AllSigned -File '<protected-staging>\Setup-Tester.ps1' -ManifestSHA256 '<independent-envelope-sha256>' -TrustedPublicKeySHA256 '<owner-ed25519-key-sha256>' -TrustedPublisherSHA256 '<owner-raw-certificate-sha256>' -ExpectedSourceSHA '<exact-40-character-source-sha>' -Mode production
```

Do not import a package certificate or relax execution policy to make installation run. No production owner certificate/key is available in this candidate environment; production AllSigned and signing acceptance remain NOT RUN.

The canonical destination is `C:\TesterLab`. Do not install over the existing ADMINIT installation without owner authorization. Setup performs preflight, verifies the signed complete inventory, creates the private virtual environment, installs hash-locked offline wheels, initializes the owned PostgreSQL cluster, migrates the three dedicated databases with owner credentials, registers native services and checks readiness. Preserve the installation state/log on failure and rerun only after ownership and recovery state are understood.

Two desktop shortcuts are intended: **TESTER — Запуск** starts owned services and opens the ready terminal; **TESTER — Удаление** invokes the owned uninstaller. Repeated launch must retain a single service process. Service lifecycle helpers live in the installed release's `scripts/windows` directory. Read the installed helper's PowerShell parameters rather than inventing service names; names and paths are installation-UUID-bound.

Removal displays the owned resources and requires exact `DELETE TESTER` followed by `YES`. It irreversibly removes the owned cluster, results, market history, configuration, logs and shortcuts. Unknown ownership, altered service identity, links or foreign contents must cause refusal. There is no retained uninstall backup. Exercise removal only on a dedicated temporary installation during acceptance.

For diagnosis preserve source SHA, envelope digest, installation UUID, service status, installation journal and API/worker readiness. Keep credentials and database passwords out of published evidence. Recovery must verify owned service executable and cluster identity before restarting. System PostgreSQL port 5432 and unrelated files must remain unchanged.

Updates require a newly signed source-bound package and data-preservation acceptance; a generic automatic in-place upgrade is not yet certified. Keep production use blocked until the Windows install → recovery → reboot → data verification → uninstall → clean reinstall matrix passes. Never treat process-backend or dry-run evidence as native SCM acceptance.

The dedicated cluster contains `tester` (platform/storage/history projections), `tester_catalog` (instrument catalogue), and `tester_research_release` (the persisted research ledger). Their App logins are separate nonowner roles; migration credentials remain private and never become API/worker administrator credentials. Startup is not a migration or credential-issuing operation. Research schema preparation does not enable a producer, publish synthetic data, issue capabilities or certify real-market execution.

The default native profile uses the existing reference-prices reader/handler and catalogue adapter. Optional research projection uses the existing application composition, fenced persisted dispatcher and worker, with an explicitly scoped research App-role DSN. It defaults off unless configured. Operator-issued, expiring/revocable capabilities and verified artifacts are required. The UI retains SIMULATED / SYNTHETIC ONLY labels; route availability is not real-market strategy certification.

Host/API/worker restart admission is owned by the dedicated PostgreSQL policy and its installation/source-scoped counters. Runtime identities execute restricted reservation functions rather than edit/reset that policy. Files under the runtime tree are diagnostic receipts, not an alternate authority or fallback budget. Exhaustion and unavailable authority fail closed; an owner decision is required to change policy or authorize recovery.
