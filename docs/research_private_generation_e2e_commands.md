# RESEARCH #71 — private MASTER generation acceptance and exact combined-head replay

**Source-only draft. No physical PC4, no installed Windows services, no
PostgreSQL 5432, no Codex, no trading, no main or MASTER PR77 modifications.**

## What is actually pinned

The reviewed source ledger
`docs/research_private_generation_source_ledger.json` is an immutable
Git file identified by **blob `1447406f01ff1c200059a89a7556dd404be3f9be`**.
It names **23 exact Git blobs**, each with a role, full 40-character source
commit SHA and exact regular-file mode. This is a code-reviewed Git-object
provenance ledger, **not an externally signed source attestation**.

- PC4 baseline: `3b2eef8a4139195db1a775545e83f1c1f58bf403`
- MASTER docs/composer: `5ee448fa6e0c356034e33b6f54f2ddbf6bdbc1cd`
- DATA #72: `ce4467dd5f22f89c9f691cecb768f2c8cf9d606a`
- RESEARCH #71 runtime and current negative tests: `3966e5465b0b7881da4d23883ec82157fcd54aa1`
- UI #74: `f88d374f1ffb1f3a9a2e014f16ef8b266854063a`
- OPS private generation prototype: `113d7f3b97f47c86a19dc8590bdbb9809138c4d8`
- The reviewed OPS module's exact Git blob: `1a28603452efaaf9d69e4446b5ee2bf8bf7a5d9e`

The RESEARCH branch's `scripts/research_private_generation_issue59.py`
reads these **exact Git blobs** with Git `show` after independently
verifying `ls-tree` mode, expected blob SHA and bytes. It creates a private
0700 directory only inside disposable `RUNNER_TEMP`, invokes the pinned
OPS `publish_generation`, independently checks `read_current` and
`read_pinned`, then loads the DATA/RESEARCH modules from the *sealed private
generation* for exact arithmetic and evidence-negative tests. No active
checkout source file is written by the publishing code.

## Current E2E — one immutable source-pin set, not one combined commit

On hosted Actions, the new
`.github/workflows/research-private-generation.yml` performs:

1. Read-only checkout of RESEARCH PR71 exact head and the reviewed OPS
   prototype. Verify every full source commit SHA and the 23-file ledger
   blob SHA before any source publication.
2. Provision **only** PostgreSQL16 on 127.0.0.1:55476, DB
   `tester_research_data_disposable`. No reference to live user PG5432.
3. Verify source Git blob modes/hashes, publish immutable private generation,
   confirm `CURRENT` points to a complete tree, and independently pin
   its generation ID.
4. Evaluate unknown-side volume, known Delta/CVD, exact VWAP and POC,
   bar-level Footprint, receipt tampering, incorrect dataset revision,
   late source observation, and `require_strategy_lab_f_input` refusal.
5. Execute the original RESEARCH arithmetic and composed negative tests
   **loaded from that private generation**, including real disposable
   PostgreSQL readback. Preserve a JSON receipt in the hosted run artifact.
6. Verify the prototype still refuses generation deletion without
   cross-process reader leases. Never activate native launcher or real F.

**Known trust limits:** The OPS prototype does not externally attest the
runner/caller, does not prove power-loss/fsync-after-CURRENT durability,
does not guarantee adversarial same-UID writer safety after proof,
and does not provide safe multi-reader garbage collection. A Git blob
manifest hash is not an operator signature. Consequently a green isolated
source-generation test cannot mark the prototype production-ready.

## Commands for a future true combined commit SHA

MASTER currently has **no complete committed DATA+RESEARCH+UI+OPS source
tree SHA** for full release acceptance. `5ee448fa` is not such a SHA.
After MASTER explicitly publishes/reviews an immutable combined commit
and full Git tree SHA, run these commands **only on fresh disposable hosted
runners**. Never target the installed PC4:

```bash
set -euo pipefail
test "$GITHUB_ACTIONS" = true
test -n "$EXPECTED_COMBINED_SHA"
test -n "$EXPECTED_COMBINED_TREE_SHA"
git checkout --detach "$EXPECTED_COMBINED_SHA"
test "$(git rev-parse HEAD)" = "$EXPECTED_COMBINED_SHA"
test "$(git rev-parse 'HEAD^{tree}')" = "$EXPECTED_COMBINED_TREE_SHA"
git diff --quiet
git diff --cached --quiet

python -m pip install -e '.[dev]' -c requirements-platform.lock
python -m pytest -q \
  tests/platform/test_research_metrics_issue59.py \
  tests/platform/test_data_research_adapter_issue59.py \
  tests/platform/test_research_preview_issue59.py \
  tests/platform/test_research_preview_adapter_issue59.py \
  tests/platform/test_research_composed_negative_issue59.py
```

Provision a disposable PostgreSQL16 job DB on host port **55476** and
set `TESTER_RESEARCH_DATA_TEST_DSN` to that exact local service only:

```bash
python -m unittest tests.test_execution_contract_bridge -v
python -m unittest tests.test_composed_data_research_postgres -v
python -m pytest -q \
  tests/platform/test_data_research_adapter_issue59.py \
  -k "actual_data72 or disposable_postgres or disposable_pg"
python -m pytest -q \
  tests/platform/test_research_composed_negative_issue59.py \
  -k actual_postgres
```

On the same exact combined source SHA, and distinct **disposable** PG14/PG18
storage services with version-specific `TESTER_TEST_ADMIN_DSN`, run:

```bash
python -m unittest discover -s tests/storage -v
python -m unittest tests.integration.test_trade_http -v
```

Run the existing UI FastAPI/Chromium and Combined F-G-D fixture workflow
against that **same combined SHA** with its own isolated PG16 port 55472,
actor authentication and digest-bound G/E acknowledgement. Such a synthetic
G/E PASS does not lift real F's `UNKNOWN` coverage refusal.

### Required negative gates / expected results

- Tampered original HTTP body/page/row hashes or provenance: **DENY**.
- Inconsistent generation receipt, pointer or Git object: **DENY**.
- Forged B revision/historical known-at or lookahead: **DENY**.
- UNKNOWN taker side: retain exact volume, never invent Bid/Ask.
- Footprint totals and known Delta/CVD, exact rational VWAP, deterministic
  volume POC: **PASS only with verified observed source**.
- Claim of full market coverage or real Strategy Lab F admission:
  **DENIED_UNKNOWN_COVERAGE**.
- Concurrent writer, crash durability, independently sealed caller identity
  and safe reader GC: **BLOCKED** until externally validated.
- Real Bybit HTTP403 remains `ACCESS_BLOCKED`, not an empty history proof.

**Release result remains NO_GO** until MASTER owns one immutable combined SHA
and all independent PostgreSQL14/16/18, FastAPI/Chromium, security, native
Win32 and physical acceptance gates. No native PC4 restart is authorized.
