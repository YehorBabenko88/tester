# TESTER unified integration implementation plan

> For agentic workers: execute independent installer and proposal bridge workstreams; root owns assembly, source pins, CI and publication.

**Goal:** assemble the five approved SHA snapshots, validate proposal coordination and real disposable Windows lifecycle, publish a separate Draft PR.

**Architecture:** start from MASTER, merge child owner files without rewriting branches. One pure classifier feeds one durable journal. Existing supervisor owns API and worker. Windows acceptance uses only a fresh owned temporary root, dedicated PostgreSQL and fixture Desktop; canonical C:\TesterLab is prepared, never changed.

**Tech Stack:** Python 3.12, pinned requirements-platform.lock, Ruff 0.17.4, PostgreSQL 18, PowerShell/Windows.

**Spec:** user request and MASTER Issue #88, latest approved five SHAs.

## Global constraints
GUARDIANS_ENABLED=false; no trading, main merge, branch deletion, live C:\TesterLab change or system PostgreSQL 5432 mutation.

## Review focus
Admitted scope cannot be substituted to evade leases or restart budgets. Foreign/reparse/tampered installation paths fail closed. No destructive action before both confirmations. Installed identity is never relabeled tester_preview. Source manifest and CI must bind exact published SHA.

## Tasks
- [x] Create isolated worktree, merge exact child snapshots; inspect disjoint owner paths and conflicts.
- [ ] Add immutable source manifest and verification gate; reject changed pins/owner bytes and missing ancestry.
- [ ] Add combined four-Guardian bridge tests and fix confirmed scope-binding defect with regression proof; preserve default-disabled behavior.
- [ ] Add real disposable Windows installation/start/stop/uninstall and exact shortcut tests; reuse filesystem/process primitives and existing API/worker supervisor, no second effectful Guardian.
- [ ] Run compileall, Ruff check/format-check, all pytest and acceptance integration/security on own cluster.
- [ ] Independently review candidate, resolve findings, publish Draft PR against MASTER, inspect every applicable exact-head workflow to PASS.
- [ ] Publish full SHA, pins, CI links, actual Windows lifecycle evidence and deployment boundary in Issue #88.

Ruling: audit found missing Storage policy bridge and caller-supplied durable scope; fix them in integration only and record overrides rather than changing frozen MASTER branch.
Ruling: installed runtime remains Guardians-disabled; tester_preview proposal fixtures do not authorize tester_lab execution. Real disposable process acceptance is distinct from SCM/boot or installed-host deployment evidence.
