# DATA #72 → Private Generation Composer: source-only review and PG14/18 replay

**DRAFT / NO_GO**, 2026-11-14. This material belongs only to DATA PR #72. No changes to MASTER PR #77, main, PC4, installed PostgreSQL port 5432, native launcher, real orders or Bybit.

## Reviewed immutable source

[The 19-file Git blob manifest](data72-private-generation-blob-manifest.json) identifies each exact path, Git object SHA1 and regular-file mode of the **MASTER-pinned DATA source** commit \`d5bfdad445a1eefb9dc12bf2725a00f171e2c321\` relative to PC4 base \`3b2eef8a4139195db1a775545e83f1c1f58bf403\`. The current DATA driver was initially reviewed at \`ce4467dd5f22f89c9f691cecb768f2c8cf9d606a\`; subsequent DATA-only changes add these tests and CI, not a new reviewed MASTER source set. Runtime files are:

- \`src/ingest/execution_contract.py\`
- \`src/ingest/public_auxiliary.py\`
- \`src/ingest/public_auxiliary_runner.py\`
- \`src/storage/models.py\`
- \`src/storage/migrations/008_public_unknown_taker_side.sql\`

The remaining 14 entries are test, workflow and documentation files, including shared \`tests/platform/test_research_postgres.py\`. RESEARCH's matching Git blob is \`402c25410ba4cc6704911148b1ec0c5bba720088\`. The canonical migration 008 Git blob is \`05600c50e3d4598aaa4645ac58e137a719b0eed2\`. The migrator uses checksummed, serialized and ordered SQL migrations; in the reviewed tree only versions 001–008 exist. Historical source rows are append-only; 008 broadens **only** the two trade-side constraints to \`('Buy','Sell','Unknown')\` while retaining positive price/size, nonblank trade ID and native side uncertainty. Never edit historical rows or the recorded migration checksum.

## Private composer — which proof is real?

OPS PR #73 prototype source is pinned to \`113d7f3b97f47c86a19dc8590bdbb9809138c4d8\`, Git blob \`1a28603452efaaf9d69e4446b5ee2bf8bf7a5d9e\`. The DATA CI job \`data-source-generation\` loads that exact module from **RUNNER_TEMP** and reads all 19 exact source blobs from Git. It verifies the blobs against the checked-in DATA manifest, builds the prototype's \`ReviewedBlob\` set and canonical schema, and generates two **private 0700** generations under a new isolated temporary directory, never in the active checkout. It checks the complete verified receipt and CURRENT reader, keeps an earlier pinned generation readable, refuses GC, injects a failure before CURRENT switching, and corrupts an **isolated generated copy** to demonstrate rejection. It compares original checkout file hashes before and after. The workflow checks that source files retain their original Git object identity and that there are no database DSNs; no database service is provisioned.

**Boundary:** The canonical private-generation manifest SHA is constructed inside the test. It is therefore **self-attested** and *not* a trusted external manifest signer or Git caller attestation. A compromised runner/same-UID writer, parent-directory relocation, post-CURRENT fsync power-loss durability, cross-process reader leases, generation garbage collection and native launcher admission remain **BLOCKED**. Passing this isolated proof cannot authorize a private-generation switch in MASTER or production.

GitHub Actions source-only reproduction:

\`\`\`bash
# Only inside the disposable GitHub Actions job configured by
# .github/workflows/data-private-generation-review.yml.
# The action verifies exact DATA/OPS SHAs and materializes OPS source into
# RUNNER_TEMP (not the checkout).
python -m unittest tests.test_data_private_generation_compat -v
\`\`\`

Do not emulate \`GITHUB_ACTIONS\`, \`CI\`, runner identity, or any other attestation flags on a local or production computer.

## Reproduce actual DATA → PostgreSQL → RESEARCH E2E on one frozen source tree

The existing [.github/workflows/public-auxiliary-data.yml](../../.github/workflows/public-auxiliary-data.yml) defines \`master-composed-data-postgres\` as an independent **PG14/PG18, fail-fast:false** matrix on a newly created isolated database service. Each job:

1. Checks out immutable reviewed MASTER staging \`88ad7a7a82f35d1829b5eb07c0c1d4878741f527\` and reads the latest DATA test driver from a separate checkout.
2. Fetches PC4 base, pinned DATA \`d5bfdad4\`, RESEARCH \`7ec35810\`, UI \`a61a2c3b\`, OPS \`0d4649f3\`, verifying full exact commit SHAs. The authorized CI composition step must run before any tests; a future hardened composer requires real runner attestation, not forged environment variables.
3. Validates all 19 source blobs, migration-008 normalized SQL SHA256, shared PGHOSTADDR blob and all historical predecessor versions before a database write.
4. Uses only a disposable PostgreSQL service **exposed to runner loopback port 55479** with database \`tester_aux_ci\` and CI-only user \`tester_aux_ci\`. The driver rejects any other host, port, user or database, explicit \`hostaddr\`, \`service\` and connection options.
5. Runs v1→008 historical preservation/checksum tests; creates three unique disposable test databases, each with independent recorded original HTTP fixture and fresh connection; runs execution bridge, public OI/funding/trades, provenance, Decimal, unknown-side and RESEARCH reader tests. Drops the three test databases on completion. Neither test nor generator writes to the existing base database, installed 5432 or PC4.
6. Leaves live Bybit disabled. Public HTTP403 is ACCESS_BLOCKED, not replayed as successful live data.

Exact commands **inside that configured ephemeral GitHub matrix job**, after an externally verified source composition and isolated PostgreSQL service startup:

\`\`\`bash
# PGVER is selected by matrix: 14 or 18.
test "$MASTER_COMPOSED_DATA_TEST" = true
test "$(git rev-parse HEAD)" = "$MASTER_STAGING_SHA"
test "$TESTER_AUX_LIVE_BYBIT" = no
python .ci-data-driver/tests/test_master_composed_data_integrity.py -v
python -m pip install -r requirements-dev.txt
python .ci-data-driver/scripts/run_master_composed_data_postgres.py
\`\`\`

The actual prior [PG14/PG18 combined DATA CI run](https://github.com/YehorBabenko88/tester/actions/runs/43584749302) **passed both matrix cells** at DATA \`ce4467dd...\`: each ran 6 original/bridge tests, 2 migration preservation/checksum tests, 2 execution tests, 5 public auxiliary tests and 2 DATA→RESEARCH tests, reporting \`COMPOSED_DATA_PG_ALL_SUITES_PASS 3\`. [Storage Integrity PG14/18](https://github.com/YehorBabenko88/tester/actions/runs/43584749366) passed all four matrix cells. These are **real disposable PostgreSQL** proofs, but input comes from an original HTTP fixture, not an authorized live Bybit request.

## Required exact data semantics

The following checks must remain true on **each tested immutable source revision**:

- **Original HTTP**: SHA-256 of unchanged HTTP response bytes, immutable page/row receipts, original native \`execId\`, strict provenance; a detached SHA tamper must be rejected and append-only evidence may not be updated.
- **Decimal**: source-native exact decimal quantity and price; no float rounding; inverse contract remains noncomparable.
- **Time**: preserve source \`event_ms\`, locally observed \`observed_ms\`, unknown \`publication_ms=None\` and \`historical_known_at_ms=None\`; no look-ahead.
- **Unknown taker side**: raw Unknown is never invented into Buy or Sell; known delta/CVD exclude unknown volume, while observed VWAP/POC/footprint retain volume with incomplete side classification.
- **Coverage**: incomplete pages, missing periods, insufficient samples and publisher denial never become COMPLETE. F-admission remains false; the reader cannot claim an atomic dataset revision not actually persisted.

**NO_GO release blockers:** externally attested caller and immutable external manifest signature, adversarial concurrent-writer isolation, power-loss durability after CURRENT switch, safe multi-reader generation GC, full exact four-producer integration, physical tests and real Bybit HTTP403. The DATA source review and PG14/18 gates do not prove these boundaries.
