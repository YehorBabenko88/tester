# OPS #73 → MASTER #77: exact combined SHA test commands (2026-11-14)

**REVIEW-ONLY PLAN — NOT_EXECUTED.** These commands are meant for a **fresh,
isolated GitHub Actions runner**, NEVER the installed PC4, LS5, ADMINIT or the
user's PostgreSQL cluster. They do not install the native launcher. The
staging PR #77 now includes a GitHub Actions-only uncommitted source composer;
its first smoke test compiled 125 transferred files and checked 325 Python
ASTs, but there is **no immutable combined application commit/tree SHA** for
full release acceptance yet. Never use PR #77's docs-only HEAD as if it
represented a composed DATA+RESEARCH+UI+OPS binary.

## Evidence required before any job

MASTER must prepare an actual composed candidate in a new branch from
`3b2eef8a4139195db1a775545e83f1c1f58bf403`, with exact full 40-character
producer commit hashes, full combined commit SHA and Git tree SHA, after
manually resolving the DATA/RESEARCH conflict in
`tests/platform/test_research_postgres.py`. Preserve **both** PGHOSTADDR
redirection regression cases and distinguish Docker's `inet_server_addr()`
from the published host address. Do not modify frozen `contracts/v1` to
silence Kind drift. Keep UI #70 ancestry and pin DATA's migration 008 checksum.

The source-only handoff `ops-77-native-source-handoff.json` records the
original frozen producer pins; if MASTER selects newer producers it must
publish a NEW reviewed pin set, not change the old JSON silently.

On the fresh runner, BEFORE installing dependencies or touching its
**disposable** PostgreSQL services:

```bash
test "$GITHUB_ACTIONS" = true
test -n "$EXPECTED_COMBINED_SHA"
test -n "$EXPECTED_COMBINED_TREE_SHA"
test "$(git rev-parse HEAD)" = "$EXPECTED_COMBINED_SHA"
test "$(git rev-parse 'HEAD^{tree}')" = "$EXPECTED_COMBINED_TREE_SHA"
git diff --quiet
git diff --cached --quiet
```

This is a hard stop, not a warning. All later checks MUST use the same SHA;
rerunning against a moving branch is not full candidate acceptance.

## Commands to run on one exact combined checkout

Python 3.12, isolated Linux CI runner:

```bash
python -m pip install -r requirements-dev.txt
python -m compileall -q .
python -m unittest discover -p "test_*.py" -v
python scripts/acceptance.py
```

Platform/security (Python 3.11 and 3.14 matrix, **disposable PG16 only**,
`TEST_DATABASE_URL` from the CI service, never the user's database):

```bash
python -m pip install -e '.[dev]' -c requirements-platform.lock
python scripts/acceptance.py --integration --security
```

Storage migration, upgrade, provenance, backup and B→E source tests
(FOUR independent PG14/PG18 × Python3.11/3.13 jobs). Each job must provision
its own ephemeral PostgreSQL service and set `TESTER_TEST_ADMIN_DSN` to that
service, not to PG5432 on the user's machine. Prefer published CI host ports
55480 (PG14) and 55481 (PG18). Use the matching PG client for each version:

```bash
python -m pip install -r src/storage/requirements.txt
python -m unittest discover -s tests/storage -v
python -m pip install -e . httpx
python -m unittest tests.integration.test_trade_http -v
```

Original public HTTP→durable DATA→RESEARCH→F-denied, synthetic F/G/E, exact
pinned research PostgreSQL (isolated PG16 port 55472 and
`TEST_RESEARCH_DATABASE_URL`; the user PG5432 is not touched):

```bash
python -m pip install -e '.[dev]' -c requirements-platform.lock
python -m pytest tests/platform/test_research_postgres.py tests/platform/test_combined_routes.py -v
python -m pytest tests/platform/test_research_execution.py tests/platform/test_research_summary.py tests/platform/test_pc4_runtime.py tests/platform/test_research_execution_postgres.py -v
python -m pytest tests/platform/test_qualified_snapshot.py tests/platform/test_snapshot_repository.py tests/platform/test_terminal_snapshot.py -v
```

Frontend Node 22, real isolated Chromium with the UI 13-timeframe fixtures:

```bash
cd frontend
npm ci --ignore-scripts
npm test
npx playwright install --with-deps chromium
npm run test:ui
```

Combined FastAPI → synthetic F/G/E → Chromium browser ACK needs the existing
**same-job server orchestration** in
`.github/workflows/combined-synthetic-browser.yml` — its `serve` command
is long-running and MUST NOT be run sequentially in a plain shell.
With a CI-owned API process, exact `TESTER_COMBINED_FIXTURE_FILE`,
`TESTER_COMBINED_CI=synthetic-disposable` and ephemeral
`TEST_RESEARCH_DATABASE_URL`, run the following ordered steps with
CI-managed graceful cleanup:

```bash
python -m pytest tests/platform/test_combined_browser_fixture.py -q
python -m scripts.combined_browser_ci prepare "$TESTER_COMBINED_FIXTURE_FILE"
# Start 'python -m scripts.combined_browser_ci serve ...' as CI-owned background process
python -m scripts.combined_browser_ci gateway "$TESTER_COMBINED_FIXTURE_FILE"
(cd frontend && npx playwright test -c playwright.combined.config.js --reporter=line)
python -m scripts.combined_browser_ci verify "$TESTER_COMBINED_FIXTURE_FILE"
```

OPS read-only pure, fenced PostgreSQL16 (dedicated service host 55479),
real Windows-2025 process and launcher fixture tests:

```bash
python -m unittest discover -s tests/platform -p 'test_ops_native_scoped_stop.py' -v
python -m unittest discover -s tests/platform -p 'test_ops_native_optin_handoff.py' -v
python -m unittest discover -s tests/platform -p 'test_ops_combined_ci_plan.py' -v
python -m unittest discover -s tests/platform -p 'test_ops_candidate_compatibility.py' -v
# With TEST_OPS_ISOLATED_DSN and disposable PG16:
python -m unittest discover -s tests/platform -p 'test_ops_pg_real.py' -v
python -m unittest discover -s tests/platform -p 'test_ops_pg_lifecycle_real.py' -v
python -m unittest discover -s tests/platform -p 'test_ops_pg_dual_real.py' -v
python -m unittest discover -s tests/platform -p 'test_ops_pg_load_real.py' -v
python -m unittest discover -s tests/platform -p 'test_ops_native_admission_pg_real.py' -v
# On fresh Windows-2025 runner with GITHUB_ACTIONS=true ONLY:
python -m unittest discover -s tests/platform -p 'test_ops_native_windows_process.py' -v
python -m unittest discover -s tests/platform -p 'test_ops_native_fixture_launcher.py' -v
```

Windows native installer/SCM is tested **only** in an isolated disposable
hosted Windows runner, NOT on PC4. The corresponding existing workflow uses
`pwsh -File ./scripts/windows/Test-Isolated.ps1`; it has no permission to
activate the OPS native launcher on an installed host.

## Command-plan generator (never executes these commands)

Given an actual combined SHA, its tree SHA and the reviewed source-only
handoff JSON, use:

```bash
python -m scripts.ops_combined_ci_plan \
  --combined-sha "$EXPECTED_COMBINED_SHA" \
  --combined-tree-sha "$EXPECTED_COMBINED_TREE_SHA" \
  --ops-sha "$PINNED_OPS_SHA" \
  --handoff-json ops-77-native-source-handoff.json \
  --producer-pins-json candidate-producer-pins.json \
  > ops-combined-ci-plan.json
```

The `candidate-producer-pins.json` must include exactly the reviewed frozen
DATA, RESEARCH, UI and OPS full commit SHAs. Any changed DATA/RESEARCH/UI pin
requires a new MASTER-reviewed manifest. The generator validates all source
pins and scope, emits stage dependencies/commands and a SHA256 for the
**plan**, and always says `execution=NOT_RUN` and
`installed_native_activation=BLOCKED`. It has no execution mode.

## Acceptance gates — every one mandatory

- All of the above tests must run against the **same** combined source SHA,
  with explicit pass/fail/skip and archived PG14/18/16, Win32, FastAPI,
  Chromium and security evidence. A green standalone producer is not enough.
- CPU/RAM/I/O observations must retain exact original child identity,
  measured sample windows and unknown fields. PostgreSQL WAL growth must be
  measured within the same postmaster incarnation. No hard OS resource limits
  are claimed from short tests.
- Both API and worker must use generation-scoped, birth-enrolled cooperative
  stop *before* any installed native integration; externally sealed dual
  zero exits, original handles and checkpoint evidence must survive audit.
- A dead controller without an independent protected handle owner must
  **refuse restart**, not adopt PIDs. Partial startup rollback must preserve
  the checkpoint and all queued work; active/UNKNOWN lease or changed PG
  incarnation must deny a new generation.
- **Never automatically import, install or start OPS launcher** in normal
  packaging, API/worker, `pc4_runtime`, LocalService, Windows SCM or a
  background updater. This integration is review-only until the separate
  physical installation is explicitly approved.
- Live Bybit 403 stays `ACCESS_BLOCKED`, real historical coverage UNKNOWN
  and real Strategy Lab F admission NO_GO. Do not reinterpret fixture PASS as
  official exchange completeness or real trading readiness.

**Result as of this document:** combined exact-head test suite
**NOT_RUN** because #77 does not contain a composed source tree.
Native installed PC4 restart **BLOCKED**. Full staging release **NO_GO**.

## Current OPS-only composer smoke evidence

The MASTER composer source pinned at
`4e64ec9f6a1da453019b68e598e235036701739f` has also been exercised
by the OPS branch on fresh disposable CI runners:
[OPS run 43578944193](https://github.com/YehorBabenko88/tester/actions/runs/43578944193)
ran real Win32 birth-bound dual-process/rollback tests on Windows-2025
and real isolated PostgreSQL16 queue/lease/WAL tests after test-only composition.
Both jobs **PASS**, but the composer still transports 30 OPS files outside
the minimal opt-in review list. The negative audit is an intentionally
successful test that returns `BLOCKED_SCOPE_TOO_BROAD`; it must not be
reinterpreted as approval of an installable native launcher.

Master source currently checks `CI=true` and an exact staging SHA, not a
hardware-backed machine identity. The supplied GitHub Actions workflow ran
on disposable hosted infrastructure. Before any reuse outside that workflow,
MASTER should add independent explicit runner/workspace validation and
symlink-safe destination checks. **Never** run the composer against the
installed PC4 checkout. Those further hardening requirements do not change
the fact that no PC4 process or production PostgreSQL was touched by this test.
