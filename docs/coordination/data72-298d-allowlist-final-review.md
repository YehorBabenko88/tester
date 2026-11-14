# DATA #72 immutable admission review for MASTER 298d6107

**Status: REVIEW CANDIDATE — NOT approved for MASTER #77; NO_GO.**

Frozen objects:

- MASTER `298d610771d962ee7b796eac1834d74138768fac`
- DATA HEAD audited `4581e415bd8979a4debee1eb56120bb21b9d95fd`
- BASE `3b2eef8a4139195db1a775545e83f1c1f58bf403`
- Previous DATA source `d5bfdad445a1eefb9dc12bf2725a00f171e2c321`
- Previous pinned 19-file DATA manifest is **not** equivalent to the
  current 25-file branch diff. Do not silently repin it.

Full Git tree entries, Git blob SHA1, regular modes and category are frozen
in `data72-current-head-immutable-review.json`. Run
`python scripts/verify_data72_head_manifest.py` from an isolated checkout
with Git access to the immutable BASE, MASTER and DATA commits. The script
checks exact diff set, each git ls-tree path/mode/blob, original Git bytes,
21 existing allowlist paths and exactly four additional paths. It never
connects to PostgreSQL or edits the checkout.

## Four independently inventoried new files

| Path | Git blob SHA1 | Category | Review |
|---|---|---|---|
| `.github/workflows/data-private-generation-review.yml` | `0d7ffed1a52d51f8da748204e46441ab768e3d35` | CI workflow | CI-only; not runtime |
| `docs/coordination/data72-private-generation-blob-manifest.json` | `f1f60a8af7eb60a83c3367c30e73bae2a4e33ac2` | Review ledger | 19 older frozen source files; self-attested |
| `docs/coordination/data72-private-generation-replay.md` | `7c62363259c8918e245ef7f9a1103da866818d4c` | Documentation | PG replay instructions and explicit trust blockers |
| `tests/test_data_private_generation_compat.py` | `815f13387e013f25e63b6309ee95cbcd630e493a` | Test | 19-blob source-only private staging; no DB |

**Decision for MASTER:** leave all four `PENDING_INDEPENDENT_REVIEW`.
No new runtime modules among the four; preserving the original 21-path
MASTER source allowlist continues to require pinning the prior DATA source.
Reviewing current DATA as a whole requires an independent 25-path approval
and immutable digest, not a mutable worktree manifest.

## Previous exact-head DATA PG evidence

- [Public auxiliary DATA contracts, run 43595442118](https://github.com/YehorBabenko88/tester/actions/runs/43595442118) SUCCESS. Its PG14 and PG18 Unknown-side jobs and both
  `master-composed-data-postgres` matrix jobs succeeded. Source-only offline
  compatibility and PostgreSQL16 source-receipt tests also succeeded.
- [Storage integrity run 43595442124](https://github.com/YehorBabenko88/tester/actions/runs/43595442124) SUCCESS on both PG14/18 × Python3.11/3.13.
- Canonical migration 008: Git blob
  `05600c50e3d4598aaa4645ac58e137a719b0eed2`. Unique ordered SQL
  versions 001..008 and checksum preservation. Keeps positive price/size,
  original trade id, and expands only two trade-side constraints to
  Buy/Sell/Unknown.
- The historical DATA → PostgreSQL → RESEARCH PG14 and PG18 combined source
  jobs passed using old staging SHA
  `88ad7a7a82f35d1829b5eb07c0c1d4878741f527`. The composer for this
  test writes into a **disposable** checkout and is not the private generation
  publisher. These old successes cannot be upgraded into new-head private
  generation E2E evidence.

## E2E commands on ONE future sealed private generation

Execute only inside an independently authorized, disposable GitHub Actions
matrix with PostgreSQL image `postgres:14` or `postgres:18` exposed on
**127.0.0.1:55479**, CI-only `tester_aux_ci` role/database, and trusted
external manifest pin. The exact BASE + DATA + RESEARCH + UI + OPS sources
must first have been admitted by a separate MASTER-owned composer job.

The following commands are **gated examples**; do not run until
`MASTER_PRIVATE_GENERATION_AUTHORIZED=true` is supplied by a trusted
external workflow after receipt, source and runner verification:

```bash
set -euo pipefail
test "$MASTER_PRIVATE_GENERATION_AUTHORIZED" = true
test "$TESTER_AUX_LIVE_BYBIT" = no
test "$CI" = true
test "$PGHOST" = 127.0.0.1
test "$PGPORT" = 55479
test "$PGUSER" = tester_aux_ci
test "$PGDATABASE" = tester_aux_ci

# Must point to an immutable, full, reviewed, already-published generation.
test -n "$PINNED_GENERATION_ROOT"
test -f "$PINNED_GENERATION_ROOT/generation.json"
test "$PINNED_MANIFEST_SHA256" = "$EXTERNALLY_REVIEWED_MANIFEST_SHA256"

# MASTER owner must execute read_current/read_pinned and verify_tree against
# that exact externally reviewed whole-tree manifest before these tests.
# The approved runner then tests the pinned snapshot, not mutable CURRENT.
cd "$PINNED_GENERATION_ROOT"
python -m pip install -r requirements-dev.txt

# Data receipts and SQL migration checks using the same sealed generation.
python -m unittest -v \
  tests.test_public_auxiliary \
  tests.test_public_auxiliary_runner \
  tests.test_execution_contract_bridge

python -m unittest -v \
  tests.storage.test_postgres.PostgresTests.test_upgrade_preserves_version_one_raw_data \
  tests.storage.test_postgres.PostgresTests.test_migrations_repeat_and_checksum

python -m unittest -v \
  tests.test_execution_contract_postgres \
  tests.test_public_auxiliary_postgres \
  tests.test_composed_data_research_postgres
```

For the old, already-verified per-suite isolation procedure, use the checked
`scripts/run_master_composed_data_postgres.py` in a disposable CI service
only. It enforces `127.0.0.1:55479`, CI role/database and three fresh suite
databases with cleanup; never override these protections. Test DB environment:

```bash
export MASTER_COMPOSED_DATA_TEST=true
export TESTER_AUX_LIVE_BYBIT=no
export TESTER_AUX_TEST_DSN='postgresql://tester_aux_ci:disposable-only@127.0.0.1:55479/tester_aux_ci'
export TESTER_TEST_ADMIN_DSN='host=127.0.0.1 port=55479 user=tester_aux_ci password=disposable-only dbname=tester_aux_ci connect_timeout=5'
python scripts/run_master_composed_data_postgres.py
```

The runner treats `CI=true` only as a guard, never as proof of external
Git/runner attestation. Do **not** imitate these flags on an installed PC.
Never run against PC4 or installed PG5432.

## Required assertions

Original HTTP response bytes SHA256, original page/row hashes and `execId`
must remain immutable; a forged hash must refuse projection. Use exact
source-native `Decimal` values and preserve category units. Unknown taker
side MUST NOT be fabricated as Buy/Sell. Event UTC time differs from local
observed time; unknown publication/known-at remain null. Coverage is UNKNOWN,
dataset revision UNPINNED, F admission false, and Bybit 403 ACCESS_BLOCKED.

## Final gate matrix

| Gate | Status |
|---|---|
| DATA current 25 files audited against Git tree | SOURCE REVIEW |
| Four new CI/docs/test additions | PENDING MASTER REVIEW |
| Prior PG14/18 migration and data/research suite | PASS (old staging) |
| Private-generation DATA source-only 19-file test | PASS (old prototype) |
| New 25-file full candidate against latest OPS recovery | NOT_RUN |
| Full BASE+DATA+RESEARCH+UI+OPS sealed generation | BLOCKED |
| New sealed generation PG14/18 plus Chromium | NOT_RUN |
| Independent Git/runner attestation and hostile same-UID fencing | BLOCKED |
| Production PC4/PG5432/native launcher | NO_GO |

The immutable ledger is a **DATA-proposed review artifact**, not a
MASTER-approved manifest and not an externally signed trust root.


## Frozen canonical OPS-compatible 25-file manifest

The independently reproduced canonical manifest is checked in at
`docs/coordination/data72-25file-ops-canonical-manifest.json`.

- Manifest Git blob SHA1: `f31261f2811208bc4a023cf50171a0125dfc32e0`
- Manifest SHA256: `53c31ea12d320ccfcd567fa9fca18d78ea7da060cab7063b2f001700bcde4fce`
- Source DATA commit: `4581e415bd8979a4debee1eb56120bb21b9d95fd`
- Source count: 25 regular Git blobs with exact per-file SHA256, Git SHA1
  and mode `420` (`0o644`), usable by OPS
  `verify_sources`/`publish_generation` in isolated CI.
- This digest was generated and checked **inside DATA CI**. It is NOT
  independently signed/attested by MASTER and does not approve expanding
  MASTER #77's allowlist. External pin and trust review are still BLOCKED.

`tests/test_data_25file_private_generation.py` now rejects any drift in
the canonical manifest bytes, SHA256 or Git blob.
