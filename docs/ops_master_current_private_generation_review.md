# OPS #73 — current MASTER runtime receipt into private generation (CI-only)

This is a **separate independent source-integration test**, NOT a production
composer, an installation, or a claim of release readiness.

## Frozen reviewed source

- MASTER exact candidate source commit:
  `3eb400b9d8eafe9268787dc2744721e1b83b858a`
- MASTER successful source-only workflow:
  [run 43618643208](https://github.com/YehorBabenko88/tester/actions/runs/43618643208)
  artifact `master-current-runtime-source-receipt` #14123675840
- Its exact composer Git blob: `a6c43ea8919534549acf5244753d59600f5deb77`
- Its exact reviewed allowlist blob: `08482cd45a3f46b5b8168aab2b7b19f63acb30fb`
- The normalized original receipt SHA256:
  `b8b867647eaac221a4c899045499e3a8cb4cbb273a55ef61ed756be7829f2902`
- Producer pins: DATA `327e9be91cca1aec2cd79d6d7c4846d3b024c1b9`;
  RESEARCH `af143bb73277e7bffea8ae76d84fd0ed7a707ab0`;
  UI `1d6e34907ae2e4972b8ddf2d4b7301cf0920eb3f`;
  OPS `9c0ee3a44e78d16d8beb34fbaf2973ee2e126386`.
- Immutable data ledger:
  `docs/ops_master_current_runtime_frozen_3eb400b9.json`.

MASTER's successful receipt includes **exactly 30 regular 100644 blobs**:
DATA 5, RESEARCH 4, UI 21, OPS 0. UI's 21 include some
`frontend/tests/` paths because MASTER's current source selection uses
all allowlisted `src/` and `frontend/` paths; do not call the resulting
30-file overlay a complete app or a 30-file production-only runtime package.

## Reproducible independent GitHub-only acceptance

Run the opt-in workflow
`.github/workflows/ops-master-current-runtime-private-gen.yml`
on the OPS Draft branch only. It checks out a disposable GitHub runner,
fetches exact source objects, and executes:

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m unittest discover \
  -s tests/platform \
  -p 'test_ops_master_current_runtime_private_generation.py' -v
```

The test independently recreates the Git object selection:
every reviewed path belongs to exactly the approved producer source set;
every original Git blob SHA and mode matches the frozen receipt;
cross-producer conflicts fail closed. It executes the exact original MASTER
source-only script from a disposable copy and compares its resulting receipt.
It then passes all **30 actual source blob bytes**, SHA256s, Git object SHAs
and modes into the existing OPS `ops-private-generation/1` API with a
new independent private 0700 root outside the checkout. Uses `O_EXCL`,
`O_NOFOLLOW`, file and directory fsync, sealed tree checks, unique
generation directory rename, last-step CURRENT atomic pointer switch,
reader revalidation and read-only recovery inspection.

Negative tests inject before- and after-CURRENT exceptions, post-switch
root fsync failure, receipt and file mutation, symlink/hardlink aliases,
late same-UID byte changes around publication, staging relocation,
unexpected file insertion, and invalid supplied Git blob content.
Old/current generations remain pinned; all GC requests refuse.
Tracked file digests and Git status are compared before/after. Any
unaccounted checkout change fails the job. No DB connection, browser,
service, process launcher, real trading, UAC or reboot.

## Critical limitations (do NOT conflate)

1. **Source trust boundary BLOCKED.** The test checks source content against
   Git objects and this reviewed ledger, but relies on a trusted Git reader,
   GitHub runner, and externally reviewed commit/digest pins.
   `CI=true`, `GITHUB_ACTIONS=true` and environment metadata are
   not cryptographic attestation. A fully compromised same-UID actor can
   alter paths after validation or relocate ancestor directories.
2. **Crash durability BLOCKED.** A same-filesystem atomic rename protects
   visibility of complete generations in the tested single-writer model.
   A root fsync exception after CURRENT replacement may leave the new
   pointer visible while power-loss persistence remains unknown.
   There is no actual power-failure/reboot or filesystem journal recovery
   proof. Reader validation is detection at read time, not live mutation
   prevention.
3. **No reader lease/GC.** Retiring generations is intentionally disabled.
   Reader pinning preserves a named generation and validates its bytes,
   but there are no cross-process lease or deletion fencing guarantees.
4. **Only a source overlay.** No full approved PC4 BASE tree is assembled,
   and no unified application, PostgreSQL14/18, Chromium 13TF or actual
   native restart tests execute on this private generation. No outstanding
   unreviewed CI scripts become runtime code.

Prior [OPS private-generation CI 43612716233](https://github.com/YehorBabenko88/tester/actions/runs/43612716233)
passed isolated prototype tests, but this does not prove the new
four-producer frozen candidate integration. A **new exact-head workflow
SUCCESS** is required before claiming target PASS.

Security verdict: **release NO_GO; physical PC4/PG5432 untouched;
MASTER #77/main untouched; no launcher activation**.
