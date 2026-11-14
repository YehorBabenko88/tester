# OPS → MASTER #77: exact opt-in source handoff (2026-11-14)

**Status: SOURCE-ONLY / DRAFT / NO_GO.** This document never authorizes an
installation or an installed PC4 native restart. MASTER #77 includes a test-only disposable source composer but has no
committed combined application tree. OPS has not modified MASTER #77.

## Reviewed producer pins

- Pinned base PC4: `3b2eef8a4139195db1a775545e83f1c1f58bf403`
- #77 frozen DATA #72: `f78349b1ad7bb754baee380f59bca67f2b1d2009`
- #77 frozen RESEARCH #71: `b11b79832d1b0d848773ff077a36e7cb0595fdcb`
- #77 frozen UI #74 (with #70 ancestry): `fd151168473f4add97ee286b008d4540b49ac566`
- Independently reviewed current producer pins in CI:
  DATA `d5bfdad445a1eefb9dc12bf2725a00f171e2c321`,
  RESEARCH `aeb0ad23eeb7487d93f2a6a2b3bd7f802641d844`,
  UI `a61a2c3bceec8747d7b2424d91c9a4a6c3ec9d96`.

These are two **different** source selections. Never silently substitute a newer
producer for a frozen #77 SHA; doing so requires a new immutable candidate and
explicit MASTER review. Current DATA↔RESEARCH also both modify
`tests/platform/test_research_postgres.py` (PGHOSTADDR defense): **resolve
the overlap manually; do not last-writer-wins**. No OPS path overlaps DATA,
RESEARCH or UI changed-file paths in the reviewed PRs.

## Exact proposed OPS-only file selection

Runtime source (opt-in, not installed; 12 files):
- `scripts/ops_admission_pg.py`
- `scripts/ops_candidate_compatibility.py`
- `scripts/ops_native_admission.py`
- `scripts/ops_native_fixture_launcher.py` — **CI-only**, checks Windows + GITHUB_ACTIONS
- `scripts/ops_native_optin_handoff.py` — deterministic review-only manifest
- `scripts/ops_native_scoped_stop.py` — validates a birth-bound stop; no signalling
- `scripts/ops_native_windows_rollback.py`
- `scripts/ops_native_windows_witness.py`
- `scripts/ops_pg_claim.py` — needed by real PG native-admission test, opt-in lease claim only
- `scripts/ops_pg_isolation.py` — exact disposable DSN, never user PG5432
- `scripts/ops_resource_metrics.py`
- `scripts/ops_win32_handle.py`

Focused tests (14 files):
- `tests/platform/test_ops_candidate_compatibility.py`
- `tests/platform/test_ops_combined_ci_plan.py`
- `tests/platform/test_ops_master_composer_audit.py` — read-only exact MASTER overlap regression
- `tests/platform/test_ops_master_composer_actual_negative.py` — mocked Git diff, isolated symlink/traversal/CI-spoof reproductions
- `tests/platform/test_ops_master_composer_hardening.py` — negative guard tests in disposable TemporaryDirectory
- `tests/platform/test_ops_native_admission.py`
- `tests/platform/test_ops_native_admission_pg_real.py`
- `tests/platform/test_ops_native_fixture_launcher.py`
- `tests/platform/test_ops_native_optin_handoff.py`
- `tests/platform/test_ops_native_scoped_stop.py`
- `tests/platform/test_ops_native_windows_process.py`
- `tests/platform/test_ops_native_windows_rollback.py`
- `tests/platform/test_ops_native_windows_witness.py`
- `tests/platform/test_ops_resource_metrics.py`

CI / docs (8 files):
- `.github/workflows/ops-portable.yml`
- `scripts/ops_combined_ci_plan.py` — pure, non-executing full combined-SHA CI matrix
- `scripts/ops_master_composer_audit.py` — static MASTER opt-in path gate; never launches
- `scripts/ops_master_composer_hardening.py` — proposal for CI/path/symlink guards (NOT wired into MASTER)
- `docs/ops_native_fixture_launcher_20261113.md`
- `docs/ops_native_launcher_admission_20261113.md`
- `docs/ops_native_handoff_77_20261114.md`
- `docs/ops_77_combined_ci_commands_20261114.md` — executable CI commands for future combined SHA

The list is generated in code by
`scripts.ops_native_optin_handoff.prepare_native_source_handoff`. It
requires exact reviewed source hashes and producer heads with the explicit
`PREPARE_SOURCE_ONLY` action, and returns a deterministic SHA256 manifest.

## Explicit exclusions and activation locks

**Never automatically modify:** `src/api/main.py`,
`src/api/worker.py`, `src/integration/pc4_runtime.py`, Windows services,
installer routines, startup registry, installed PC4 or any `main` file.

**Never automatically activate:** calling the review-only handoff cannot
spawn Windows processes, install services, terminate workers, reboot,
request UAC, migrate PostgreSQL or open port 5432. An `ENABLE`, `INSTALL`,
or `RESTART_PC4` action is refused. The independent
`require_live_native_activation` gate always rejects even with a valid
source plan. A source content digest is NOT an attested original Win32 handle.

Future *separately authorized* activation would require all of:
1. Per-role generation-scoped stop integrated into the **real** API and worker,
   with authenticated birth and checkpoint acknowledgement; current installed
   programs still use unscoped `stop.request`.
2. Dedicated external protected persistent guardian retaining original Win32
   handles across controller failure, sealed independent dual exit witnesses
   and unchanged process identity.
3. Atomic PostgreSQL incarnation+lease startup reservation and repeatable
   transaction/lease fencing (a post-spawn read-only probe is not an atomic lock).
4. Cross-process Windows + disposable PG test, CPU/RAM/WAL/I/O load, failed
   startup and crash recovery, and exact rollback to preserved data.
5. Separate operator authorization and physical installed PC4 acceptance.

Until these are met: `installed_native_activation=BLOCKED`,
`default_activation_enabled=false`, `ready_to_merge_or_install=false`.
This work cannot be installed by simply copying this OPS file set.

## Full exact combined SHA acceptance

The immutable combined candidate **does not yet exist** in staging PR #77.
The standalone OPS source-only handoff is ready for review, not native
installation. Once MASTER has built and approved a new immutable combined
commit/tree and resolved the DATA/RESEARCH `test_research_postgres.py`
PGHOSTADDR overlap, run the commands in
`docs/ops_77_combined_ci_commands_20261114.md` on *fresh disposable runners*.

The optional `python -m scripts.ops_combined_ci_plan` command only prints
a deterministic and SHA-bound JSON acceptance matrix; it NEVER invokes a
command. Its test suite requires immutable reviewed producer pins, rejects
an accidental user-local CI invocation and all live/native activation flags.

Source handoff selection now contains **12 opt-in runtime modules, 14 focused
OPS tests and 8 CI/documentation items**. Only after all PG14/PG18/PG16,
Windows original-handle and rollback, FastAPI/Chromium 13TF, security and
source-manifest jobs pass on the same combined SHA may MASTER consider the
next integration step. Until separately authorized physical validation,
installed native PC4 restart stays **BLOCKED**.

## Actual MASTER source composer scope — audited against immutable OPS pin

MASTER #77 at `4e64ec9f6a1da453019b68e598e235036701739f` executed its
source-only workflow [43575511227](https://github.com/YehorBabenko88/tester/actions/runs/43575511227)
with **SUCCESS: 125 changed files, 325 Python AST checks**. Its compositor
replays *all* `git diff --name-status` paths from OPS exact source pin
`0d4649f3a7cd3595ea58757f2abaf2f1b0558667`, not an OPS-specific
allowlist. It therefore copied **59 OPS paths**. This is a valid source-only
smoke test, **not proof of a minimal opt-in selection, full candidate CI,
or installed Windows service readiness**.

The revised minimal handoff explicitly includes the previously omitted
`scripts/ops_pg_claim.py`, a required direct import from the real
`test_ops_native_admission_pg_real.py`; its approved count is now
**12 runtime + 14 focused tests + 8 CI/docs = 34 files**. The frozen
MASTER OPS pin predates five read-only security-audit files, so **29 of these
34** are present in that historical source. The remaining **30 extra** OPS files
which MASTER copied but which are not in the minimal handoff are:

- `docs/ops_dual_resource_receipt.json`
- `docs/ops_issue60_followup_20261113.md`
- `docs/ops_load_and_rollback_evidence_20261113.md`
- `docs/ops_master_20261113.md`
- `docs/ops_master_resource_receipt.json`
- `docs/ops_native_windows_restart_protocol.md`
- `docs/ops_portable_checkpoint.md`
- `docs/ops_portable_checkpoint_addendum.md`
- `docs/ops_rollback_evidence_20261113.md`
- `docs/ops_win32_original_handle_20261113.md`
- `scripts/ops_disposable_api.py`
- `scripts/ops_disposable_lifecycle.py`
- `scripts/ops_dual_lifecycle.py`
- `scripts/ops_load_receipt.py`
- `scripts/ops_pg_disposable_worker.py`
- `scripts/ops_pg_snapshot.py`
- `scripts/portable_ops.py`
- `scripts/portable_ops_guard.py`
- `tests/platform/test_ops_disposable_lifecycle.py`
- `tests/platform/test_ops_dual_lifecycle.py`
- `tests/platform/test_ops_load_receipt.py`
- `tests/platform/test_ops_pg_claim.py`
- `tests/platform/test_ops_pg_dual_real.py`
- `tests/platform/test_ops_pg_isolation.py`
- `tests/platform/test_ops_pg_lifecycle_real.py`
- `tests/platform/test_ops_pg_load_real.py`
- `tests/platform/test_ops_pg_real.py`
- `tests/platform/test_ops_pg_snapshot.py`
- `tests/platform/test_portable_ops.py`
- `tests/platform/test_portable_ops_guard.py`

**Required MASTER action (owner review only):** choose whether these
30 extras are justified as independent portable/CI fixtures. For an exact
minimal native-only integration, add a reviewed, explicitly pinned OPS path
allowlist to the next disposable composer revision and refuse unexpected
OPS paths rather than copying the entire branch diff. Keep any load-test
fixtures in an isolated optional test layer, not a production bootstrap.
Do not mutate `scripts/master_disposable_compose.py` in PR #77 from OPS.

OPS now includes a **read-only** `scripts/ops_master_composer_audit.py`
with focused negative regressions and a dedicated hosted GitHub Actions
job. It compares MASTER script AST against actual Git paths and records
`BLOCKED_SCOPE_TOO_BROAD` as a deliberate, honest scope verdict,
while independently retaining source-only composition smoke `PASS`.
It does not execute MASTER's composer, connect to PostgreSQL, spawn native
processes or alter PC4.

## OPS actual composed-source validation — target CI checkpoint

The prior exact OPS branch commit `accfa4b5c39f67720b438645bfab7184557de9ba`
ran [OPS target workflow 43578944193](https://github.com/YehorBabenko88/tester/actions/runs/43578944193):
**all eight jobs SUCCESS**, including **Windows-2025 real Win32 handles
on the uncommitted 125-file MASTER source composition** (3 original-handle,
8 native fixture and 7 opt-in tests PASS) and **real disposable PostgreSQL16
on the same frozen MASTER source composition** (8 fenced lease, recovery,
rollback and CPU/RAM/WAL/I/O tests PASS). The independent source scope audit
ran 5/5 PASS while correctly returning `BLOCKED_SCOPE_TOO_BROAD`: 59 OPS
files copied, 34 currently approved, 30 extras and 5 post-pin security files absent. Those are valid **disposable tests**,
not proof of narrow opt-in inclusion, native PC4 restart or full global
PG14/PG18/Chromium source-release acceptance.

The *newer* producer compatibility audit now pins DATA
`d5bfdad445a1eefb9dc12bf2725a00f171e2c321`, RESEARCH
`aeb0ad23eeb7487d93f2a6a2b3bd7f802641d844`, UI
`a61a2c3bceec8747d7b2424d91c9a4a6c3ec9d96` for a separate
read-only source contract check. MASTER's frozen composer still uses
RESEARCH `7ec35810434e3c9159f36712a4186595ac637efa`; its newer head
changed only a workflow and tests, and retains the exact same execution
adapter Git blob. Updating latest checks does NOT silently update MASTER's
immutable producer SHA set.

### Handoff-pin consistency requirement

An owner-reviewed **new** combined producer pin is required before the
updated 34-file selection can be reproduced from Git. The current MASTER
composer pins `0d4649f3a7cd3595ea58757f2abaf2f1b0558667`, which
contains 29 of the updated selection; the five post-pin static security-audit paths
are not in that commit:

- `scripts/ops_master_composer_audit.py`
- `scripts/ops_master_composer_hardening.py`
- `tests/platform/test_ops_master_composer_actual_negative.py`
- `tests/platform/test_ops_master_composer_audit.py`
- `tests/platform/test_ops_master_composer_hardening.py`

This is an explicitly recorded SOURCE-PIN MISMATCH, **not a reason to copy a
moving OPS branch silently**. The 29 historical paths are still intact;
no required runtime imports are missing thanks to `ops_pg_claim.py`.
MASTER can either keep its frozen pin and test the historical list, or
approve a new exact OPS producer commit and only then use the 34-file
self-contained handoff, after reviewing every added audit file. Both cases
must keep installed native activation BLOCKED.

## MASTER #77 composer security assessment — user-requested negative tests

The exact source of MASTER `scripts/master_disposable_compose.py` at
`4e64ec9f6a1da453019b68e598e235036701739f` was reviewed **without
changing PR #77**. It checks `CI=true` and a pinned staging SHA and refuses
certain status/path strings, but the checks **do not authenticate a hosted
runner** and do **not** normalize/reject all relative traversal components
or follow symlink-safe filesystem writes. It performs
`(ROOT / path).parent.mkdir(...)` and `target.write_bytes(raw)`, which can
follow existing directory or file symlinks. The `PROTECTED` check compares
raw filenames and can be bypassed by unnormalized aliases in synthetic
`git diff` rows. No direct native process launch is present in the reviewed
composer. These are **source safety gaps**, not evidence of any actual PC4
compromise or an exploit from a valid Git tree.

New tests execute only the *pinned source with its `git()` replaced by a
stub* and `ROOT` redirected to a disposable `TemporaryDirectory`;
they demonstrate that malformed diff rows can reach a sibling directory,
write through a symlink, alias a protected worker path or inject an
unapproved file. They also demonstrate that `CI=true` plus a fake staging
SHA is insufficient to attest a hosted runner. No real system path is
written. The separate `require_repo_file_path`,
`require_safe_destination` and `require_ci_runner_identity` validators
propose fail-closed preflight. They are **not called by MASTER**.

**Owner review needed before non-ephemeral reuse:** require explicit hosted
GitHub runner/workspace binding, validate every normalized Git path and mode,
refuse `..`, `.`, backslashes/drive/absolute paths and unapproved files,
verify root and every path component are not symlinks, and use OS-level
no-follow, directory-fd-relative atomic writes/rename (not a racy
`resolve()`-then-write). Any such change must be reviewed and implemented
**in MASTER's own branch**, not silently imported from OPS. Until then the
verified hosted CI smoke can remain source-only; use elsewhere and native
PC4 restart are **BLOCKED**.
