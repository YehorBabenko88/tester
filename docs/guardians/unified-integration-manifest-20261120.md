# TESTER Guardian release-candidate integration gate — 2026-11-23

**Status: FOUNDATION CI GO / INTEGRATED RELEASE NO_GO.**
This is a planning manifest on a separate Draft integration branch, not
a merged Guardian build, installer, signed artifact, or authorization to
touch ADMINIT. `GUARDIANS_ENABLED=false`.

## Exact frozen commits and component ownership

| Component | PR | SHA at audit | State |
| --- | --- | --- | --- |
| MASTER foundation | #89 | `bd619eb88e9444c3b414739a27f3053fb240251e` | 8/8 exact-head required CI PASS |
| Code | #90 | `b9a27d63256a6716149540fdedecb44c773fcab9` | Draft; branch refresh + exact-head CI needed |
| Data | #91 | `aed02b49eabb7d0389026e09042a1a2343ec708f` | Draft; branch refresh + exact-head CI needed |
| Storage | #92 | `1ffb810ed68b2c9a0cb27ce1c10be182031672a0` | Draft; branch refresh + exact-head CI needed |
| Exchange | #93 | `e8b7d4a03783b3dd9df807b1c176e005600bb0d8` | Draft; branch refresh + exact-head CI needed |
| Repository consolidation | #94 | `16d794df1305c825b01852aff636d028a44c375e` | Audit only; no deletion |

The four Guardian PR diffs are **non-overlapping, four owned files each**
(module, pytest and two documents). Non-overlap alone does not certify
runtime compatibility or a safe three-way merge. The actual integrated
commit SHA is **NOT CREATED**.

## MASTER exact-head GitHub Actions evidence

All eight checks below ran against the same immutable foundation HEAD:

| Workflow | Run | Result |
| --- | --- | --- |
| Python static/unit | 45230403697 | PASS |
| Platform acceptance (Windows, Linux 3.11/3.14) | 45230403853 | PASS |
| Guardian isolated fault (Windows + Ubuntu) | 45230403627 | PASS |
| Pinned PostgreSQL integration | 45230403735 | PASS |
| PC4 qualified snapshot PostgreSQL | 45230403679 | PASS |
| Research execution with pinned PG snapshot | 45230403698 | PASS |
| Combined synthetic browser | 45230404030 | PASS |
| MASTER contract drift diagnosis | 45230403904 | PASS |

Two supplementary read-only source workflows also passed:
`45230403857` and `45230403810`. This is foundation acceptance
only, not child-branch combined validation or a physical installer test.

## Integration sequence and hard gates

1. Each child owner performs a non-force merge of the **exact approved**
   foundation SHA into its own branch. Preserve only its four owned files.
   Stop on any shared-foundation conflict; do not resolve by discarding
   upstream tests or force-pushing. Verify ancestry and run own tests.
2. Require the new child exact-head Python/Ruff, Windows/Linux Platform,
   isolated negative/fault, PostgreSQL, research, PC4, browser and contract
   workflows to complete successfully. No SKIP or QUEUED masquerades as PASS.
3. Review a cross-Guardian compatibility matrix: event schema, signed
   install identity, Service/ImagePath authority, dedupe and lease fencing,
   trusted data/Storage paths, Exchange HTTP 403/TLS/public URL restriction,
   and a single network retry owner.
4. Only after those checks, create a fresh reviewed combined integration
   commit on this **non-main** branch. Verify full content SHA and rerun
   all required exact-head CI. Do not merge child Draft PRs into main.
5. Build a signed immutable, independently pinned Windows installer rooted
   at `C:\TesterLab`: API, worker, isolated PostgreSQL on a non-5432 port,
   localhost terminal, four Guardian modules disabled, bounded boot
   restart/recovery and exclusive operation lock.
6. In a disposable Windows VM, verify installation, reinstallation refusal/
   controlled upgrade, start, stop, reboot/recovery, exactly two desktop
   shortcuts (`TESTER — Запуск`, `TESTER — Удаление`), destructive
   `DELETE TESTER` + second confirmation, every TESTER-owned DB/history/
   candles/results/config/log removal, **no retained archive or backup**,
   no junction traversal, idempotent interrupted uninstall receipt and
   unchanged system PostgreSQL 18/5432.
7. Independent physical ADMINIT review, signed artifact hashes, explicit
   human GO and only then consider controlled merge to protected `main`.

## Blocking issues (all remain NO_GO)

- Child PR exact-head CI and combined Guardian execution/after-check NOT
  VERIFIED; they have unrefreshed foundation and historical I001/E902/drift.
- The real effectful installer and full-uninstall adapter are NOT IMPLEMENTED;
  existing `windows_install_gate`/`windows_release_lifecycle` are pure
  plans, and `Write-TesterShortcutsFixture.ps1` only targets a disposable
  CI desktop.
- A separately trusted signed `tester_lab` environment identity is still
  missing; previous `tester_preview` hard-coding must not be bypassed.
- Source `src/ingest/transport.py` may retry HTTP 403 and permits broader
  Bybit host paths than Exchange Guardian. Do not merge until network
  execution and proposal policies agree, without duplicate retries.
- Independent real Windows boot/restart, full no-backup uninstall, ownership
  receipts, system PG5432 before/after and 24h soak are NOT RUN.
- Historical installers and branches in PR #94 are only *proposed* future
  deprecation candidates. No file/branch deletion authorized.

**No deploy, no trading, no Guardian effectors, no main/PR83/Preview/PG5432
changes. RELEASE NO_GO.**
