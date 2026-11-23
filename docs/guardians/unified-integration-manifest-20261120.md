# TESTER Guardian release-candidate integration gate — 2026-11-23

**Status: FIVE INDEPENDENT PR CI PASS (42/42) / COMBINED INTEGRATION AND RELEASE NO_GO.**
This is a planning manifest on a separate Draft integration branch, not
a merged Guardian build, installer, signed artifact, or authorization to
touch ADMINIT. `GUARDIANS_ENABLED=false`.

## Exact frozen commits and component ownership

| Component | PR | SHA at audit | State |
| --- | --- | --- | --- |
| MASTER foundation | #89 | `bd619eb88e9444c3b414739a27f3053fb240251e` | 8/8 exact-head required CI PASS |
| Code | #90 | `96731dfe493e5967c1336d2315fd7187381bee70` | Draft; 8/8 exact-head CI PASS, separate combined CI required |
| Data | #91 | `e7cc55a24a12ffcc9da82dda1f532fd13ae78af9` | Draft; 8/8 exact-head CI PASS, separate combined CI required |
| Storage | #92 | `a58d0d428ac003dc8d9d3b829a97aeaa115e895f` | Draft; 8/8 exact-head CI PASS, separate combined CI required |
| Exchange | #93 | `eea38f54340a943886a9ad95803626ddf5f49331` | Draft; 8/8 exact-head CI PASS, separate combined CI required |
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


## Independent source-only compatibility receipt

A separate pinned static gate verifies exact child commit ancestry against
foundation, rejects unexpected or overlapping file paths, checks regular Git
blob modes and SHA-256, compiles four Python modules/tests as AST without
importing or running them, validates expected public API names, and rejects
obvious direct runtime-effector imports. This is **NOT** a combined runtime
acceptance and does not establish signed installed identity.

- Pin manifest: `docs/guardians/guardian-static-pins.json`.
- Verifier: `scripts/guardian_pinned_static_gate.py`.
- Exact-head static workflow: https://github.com/YehorBabenko88/tester/actions/runs/45236453041 — PASS on PR95 `14752780d215ac94f82a453a29c1522b123862a9`.
- New standalone independent Windows acceptance protocol:
  `docs/guardians/adminit-independent-acceptance-20261123.md` — all
  physical rows NOT RUN, including no-backup owned-data deletion and PG5432
  before/after invariance.

The four pinned child HEADs are now independently **8/8 exact-head PASS**;
this does not prove simultaneous module operation or an accepted combined
source artifact. Codex owns the separate combined branch/PR; the GPT
integration QA gate here remains read-only.


## Guardian exact-head CI receipts — 2026-11-23

Every Guardian below has eight completed GitHub Actions with conclusion
SUCCESS at exactly the pinned SHA; foundation has ten PASS. Total: **42/42**.
These observations approve the **assembly attempt only**, not its output.

| Guardian | Python | Platform | Isolated | PG | PC4 | Research | Browser | Contract |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Code #90 | 45262615691 | 45262615952 | 45262616048 | 45262615688 | 45262616201 | 45262615689 | 45262616058 | 45262615846 |
| Data #91 | 45262622121 | 45262622055 | 45262621958 | 45262622069 | 45262622068 | 45262622117 | 45262621978 | 45262622130 |
| Storage #92 | 45262681559 | 45262681377 | 45262681433 | 45262681458 | 45262681329 | 45262681345 | 45262681163 | 45262681317 |
| Exchange #93 | 45262629428 | 45262629333 | 45262629487 | 45262629975 | 45262629771 | 45262629598 | 45262629258 | 45262629294 |

SHA and file owner checks: each child compare relative to MASTER foundation
shows exactly the own Python module, test, and two documentation files;
there is no cross-owner file-path overlap. Actual Codex assembled HEAD
remains NOT VERIFIED until independently checked with new CI.

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

- Four child PR exact-head CI now all **8/8 PASS**, but combined Guardian runtime/after-check and independent installer acceptance are NOT VERIFIED.
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
