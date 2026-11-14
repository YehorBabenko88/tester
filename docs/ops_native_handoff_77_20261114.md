# OPS → MASTER #77: exact opt-in source handoff (2026-11-14)

**Status: SOURCE-ONLY / DRAFT / NO_GO.** This document never authorizes an
installation or an installed PC4 native restart. MASTER #77 is documentation
only and remains unchanged by OPS.

## Reviewed producer pins

- Pinned base PC4: `3b2eef8a4139195db1a775545e83f1c1f58bf403`
- #77 frozen DATA #72: `f78349b1ad7bb754baee380f59bca67f2b1d2009`
- #77 frozen RESEARCH #71: `b11b79832d1b0d848773ff077a36e7cb0595fdcb`
- #77 frozen UI #74 (with #70 ancestry): `fd151168473f4add97ee286b008d4540b49ac566`
- Independently reviewed current producer pins in CI:
  DATA `739ff3f54f17082d675afbb3320112069550eb0c`,
  RESEARCH `bbca84c6f4280ece7476187d7f4dbabaf6d63542`,
  UI `d692a725b01d98e58c3cd19661dd142f47aa56c4`.

These are two **different** source selections. Never silently substitute a newer
producer for a frozen #77 SHA; doing so requires a new immutable candidate and
explicit MASTER review. Current DATA↔RESEARCH also both modify
`tests/platform/test_research_postgres.py` (PGHOSTADDR defense): **resolve
the overlap manually; do not last-writer-wins**. No OPS path overlaps DATA,
RESEARCH or UI changed-file paths in the reviewed PRs.

## Exact proposed OPS-only file selection

Runtime source (opt-in, not installed):
- `scripts/ops_admission_pg.py`
- `scripts/ops_candidate_compatibility.py`
- `scripts/ops_native_admission.py`
- `scripts/ops_native_fixture_launcher.py` — **CI-only**, checks Windows + GITHUB_ACTIONS
- `scripts/ops_native_optin_handoff.py` — deterministic review-only manifest
- `scripts/ops_native_scoped_stop.py` — validates a birth-bound stop; no signalling
- `scripts/ops_native_windows_rollback.py`
- `scripts/ops_native_windows_witness.py`
- `scripts/ops_pg_isolation.py` — exact disposable DSN, never user PG5432
- `scripts/ops_resource_metrics.py`
- `scripts/ops_win32_handle.py`

Focused tests:
- `tests/platform/test_ops_candidate_compatibility.py`
- `tests/platform/test_ops_combined_ci_plan.py`
- `tests/platform/test_ops_native_admission.py`
- `tests/platform/test_ops_native_admission_pg_real.py`
- `tests/platform/test_ops_native_fixture_launcher.py`
- `tests/platform/test_ops_native_optin_handoff.py`
- `tests/platform/test_ops_native_scoped_stop.py`
- `tests/platform/test_ops_native_windows_process.py`
- `tests/platform/test_ops_native_windows_rollback.py`
- `tests/platform/test_ops_native_windows_witness.py`
- `tests/platform/test_ops_resource_metrics.py`

CI / docs:
- `.github/workflows/ops-portable.yml`
- `scripts/ops_combined_ci_plan.py` — pure, non-executing full combined-SHA CI matrix
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

Source handoff selection now contains **11 opt-in runtime modules, 11 focused
OPS tests and 6 CI/documentation items**. Only after all PG14/PG18/PG16,
Windows original-handle and rollback, FastAPI/Chromium 13TF, security and
source-manifest jobs pass on the same combined SHA may MASTER consider the
next integration step. Until separately authorized physical validation,
installed native PC4 restart stays **BLOCKED**.
