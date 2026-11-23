# TESTER production release candidate implementation plan

> For agentic workers: independent A/B/C/D ownership, separate worktrees; root integrates and independently verifies commits. User explicitly requests autonomous implementation.

**Goal:** a source-pinned canonical native Windows release candidate, preserving PR96 and prior evidence, with real SCM, safe signed release/uninstall, unified runtime, repository consolidation and package.

**Architecture:** reuse NativeSCM, private service runtime, existing API/worker supervisor, Ed25519 verification and sole Transport. Two OS services represent postgres/API/worker logical roles. No production effectors or alternative retry/journal/SCM implementation. Actual tests use unique temporary roots/services; canonical ADMINIT remains untouched.

**Tech Stack:** Windows11/hosted Windows, CPython3.12 and supported CI matrix, PostgreSQL18/local and hosted17, pinned platform dependencies/Ruff0.17.4, PowerShell, existing static frontend/Playwright.

**Spec:** attached FINAL PRODUCTION RELEASE request; baseline PR96 e64e30d8369626197403b3962f91bb350656e4c9 and MASTER Issue88.

## Global constraints
GUARDIANS_ENABLED=false; no trading/private endpoints; no actual C:\TesterLab or systemPG5432 mutation; no real host reboot; no main merge/remote deletion/release publication/production signing until independent GO and owner's explicit final confirmation. Missing production trust key must remain a real release blocker, never replaced by test trust.

## Review focus
Signed artifact provenance and external key pin; SCM ImagePath/process/cluster identity; foreign/hardlinked/junction/raced deletion targets; partial install/uninstall recovery; bounded retry/restart and shared logical budgets; malformed inputs; preservation of baseline tests/evidence; exact published-head native/browser/data evidence. Reboot proof needs a separate disposable VM, not ADMINIT restart or process simulation.

## Ownership
A: release_install/service/SCM/lab_service hooks, Setup and start launcher, native lifecycle tests and windows-installer.md.
B: release_security/uninstall, uninstall launcher, signing/ownership/destruction tests, security-signing.md.
C: sole ingest transport/rest/public validator and Exchange identity fixes, runtime/PG/performance probes/tests, runtime.md.
D: repository inventory, proven consolidation, README/canonical release docs, deterministic package/SBOM/hashlock/build/workflow.
Root: source override/provenance gate, integration branch, combined verification, issue/PR reports and external acceptance handoff.

## Execution
- [x] Verify baseline and user spec; create integration and A/B/C/D worktrees.
- [x] Assign independent ownership/contracts; stage C/D initial audit around available concurrency slots.
- [ ] Implement and test A/B/C/D deliverables, RED/GREEN for changed verified logic.
- [ ] Integrate validated commits; declare intentional baseline source overrides with reasons/regressions and exact hashes.
- [ ] Full compile/Ruff/pytest/unit/integration/security/contract/PG/nativeWindows/Linux/browser/recovery/dependency checks and explain each SKIP.
- [ ] Build reproducible exact-SHA package, signed manifest mechanism, SBOM and owner signing instructions. No fake production signature.
- [ ] Publish one Draft release PR and achieve applicable exact-head CI PASS; distribute independent review bundle via Issue88.
- [ ] Gather independent GPT MASTER/Guardian acceptance; final evidence-based GO/NO_GO and precise remaining owner actions.

Ruling: no local hypervisor management found (vmcompute present), so separate VM access/reboot information requested asynchronously; continue all independent work while waiting.
Ruling: source pins remain immutable historical evidence, but intentional changed Guardian logic must be recorded in a versioned override manifest and regressions, not silently bypass the source gate.
