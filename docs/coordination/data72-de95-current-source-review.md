# DATA #72 exact current-head reconciliation for MASTER de95aef6

Status: REVIEW_CANDIDATE / NOT_APPROVED / RELEASE NO_GO.

The older `scripts/verify_data72_head_manifest.py` deliberately fixes DATA
`4581e415bd8979a4debee1eb56120bb21b9d95fd` to preserve its 25-file
source-only generation and immutable evidence. Its canonical manifest
`docs/coordination/data72-25file-ops-canonical-manifest.json` remains
unchanged: Git blob `f31261f2811208bc4a023cf50171a0125dfc32e0`,
SHA256 `53c31ea12d320ccfcd567fa9fca18d78ea7da060cab7063b2f001700bcde4fce`.
It is NOT current-head admission evidence.

## Separate current source audit

The new `scripts/verify_data72_current_source.py` verifies **exact**
DATA `9d7e45be0872c4c5efa17c06bc107e1dc6a0d2fb` against
MASTER `de95aef6ed46842063fc7c2ef766b7da75fe540e` and BASE
`3b2eef8a4139195db1a775545e83f1c1f58bf403`.

It enforces 31 changed paths, 21 previously approved paths and the 10
pending exact Git blobs with mode `100644`. It rejects unknown/duplicate
diff entries, source tree replacement, symlinks/gitlinks and byte mismatch.
This is a read-only Git integrity check; independent Git runner
authentication is still a separate trust-boundary requirement.

Evidence ledgers:
- `docs/coordination/data72-9d7e-source-delta.json` — all 10 pending paths,
  types, blobs and mode (NOT approved).
- `docs/coordination/data72-9d7e-minimal-runtime.json` — ONLY 5 runtime
  files from `src/`, each pinned by blob and 100644 mode, a proposal for
  MASTER review, NOT automatic composition permission.

## Minimal runtime payload

| Path | Git blob SHA1 |
|---|---|
| `src/ingest/execution_contract.py` | `8dc34f5fc73c4d9a1199efb8081be63ecdbf1d79` |
| `src/ingest/public_auxiliary.py` | `c38db1b74a1d6fbb27cb07e4192a8ef38c2c39d0` |
| `src/ingest/public_auxiliary_runner.py` | `f0f7063526c00b700533eaeac312ec608ee6ba86` |
| `src/storage/models.py` | `f63501f991bcbdb6e416a43ba39cef43a4a1f325` |
| `src/storage/migrations/008_public_unknown_taker_side.sql` | `05600c50e3d4598aaa4645ac58e137a719b0eed2` |

All five were already in the MASTER baseline allowlist. All 10 pending
paths are CI, tests, audit script or documentation, not runtime. A minimal
runtime payload is NOT a deployable whole-tree generation; the trusted
controller must add the approved BASE tree and resolve reviewed
RESEARCH/UI/OPS conflicts separately.

## Existing isolated PG gates and zero production mutations

At DATA HEAD `9d7e45be...`, GitHub Actions:
- Public auxiliary DATA contracts run `43614035012`: PASS PG14, PG18,
  old composed DATA→PG14/18→RESEARCH, migration 001..008, original HTTP
  SHA/provenance/Decimal/Unknown taker side, and four 25-file OPS source-only
  tests. Source-only manifest is self-attested.
- Storage integrity run `43614034984`: SUCCESS (isolated PG14/18).
- Pinned research PG `43614034980`: SUCCESS.
- PC4 qualified snapshot `43614035088`: synthetic CI SUCCESS; no
  physical PC4 access.
- Global Python/Platform/browser: FAIL, OpenAPI v1 Kind/source-route issues
  owned outside DATA (Issue #76 for OpenAPI).

The jobs connect to disposable GitHub PostgreSQL containers exposed on
runner `127.0.0.1:55479`, not any user machine or existing PostgreSQL
5432. Their original tested composed tree is the **older** MASTER staging
`88ad7a7a82f35d1829b5eb07c0c1d4878741f527`, NOT a newly admitted
four-producer private generation.

New same-generation PG14/18/Chromium, external Git attestation, same-UID
isolation and crash durability remain BLOCKED/NOT_RUN. No change to
existing physical database, main, MASTER #77 or PC4. NO_GO.
