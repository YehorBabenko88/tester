# MASTER source-admission checkpoint — 2026-11-14

Status: **REVIEW_IN_PROGRESS / NOT_AUTHORIZED_FOR_COMPOSITION**.

## Immutable source pins

| Workstream | PR | Reviewed HEAD | Newly unapproved paths |
|---|---|---|---:|
| DATA | #72 | `9d7e45be0872c4c5efa17c06bc107e1dc6a0d2fb` | 10 |
| RESEARCH | #71 | `af143bb73277e7bffea8ae76d84fd0ed7a707ab0` | 5 |
| UI | #74 | `2de6428b48e2b468d15aa8c9e40e4e47719e168f` | 4 |
| OPS | #73 | `9c0ee3a44e78d16d8beb34fbaf2973ee2e126386` | 17 |

The unchanged approved manifest is `docs/coordination/master-reviewed-producer-files.json`. The exact pending paths are in `2026-11-14-producer-allowlist-review-v2.md`. GitHub Actions run `43615319413` successfully produced artifact `master-unapproved-blob-inventory`, artifact id `14122365341`. The artifact is an **inventory, not admission evidence**.

## Source inspection findings

The following actual source files were fetched from the exact producer HEADs (not inferred from filenames):

- DATA `scripts/verify_data72_head_manifest.py`, blob `f88dbb6055a483367591e91caf1db28199ae523f`: read-only immutable ledger verification. **Its HEAD constant is still `4581e415...`**, not the current DATA HEAD `9d7e45be...`; it must not be treated as a current-head attestation without revalidation.
- RESEARCH `scripts/research_private_generation_issue59.py`, blob `9845ac1f3ef1be49deea4c5e32a0c9da34b5bfd3`: CI-only private-generation PG readback driver; depends on reviewed external Git source and a pinned OPS module blob.
- UI `scripts/ui74_private_generation_ci.py`, blob `c50d22a77591a637d5f0d02465eec28d0a14d9e4`: CI-only browser/private-generation probe; its `PRODUCER_PINS` point to older frozen DATA/RESEARCH/UI/OPS runtime SHAs and its MASTER pin is `5ee448fa...`. A passing run with those pins is **not** current-head unified acceptance.
- OPS `scripts/ops_private_generation_prototype.py`, blob `d8e42103c15824d7ff316314127074ee983804b3`: isolated private-generation publisher. It explicitly assumes trusted external Git blob/caller and must not be silently promoted to production.

## Admission decisions

- All **36 new paths remain unapproved**; no blanket approval based on path prefix or target CI success.
- CI-only scripts, workflows, test fixtures and documentation must not automatically become runtime payload.
- Require exact blob SHA, file mode, runtime role, independent source trust, source conflict detection and reproducible combined candidate.
- Do not silently change frozen OpenAPI v1; Issue #76 owns Kind drift.
- Preserve fail-closed UNKNOWN coverage, Bybit 403 ACCESS_BLOCKED, F denied and no real trading.
- No main merge, installed PC4, PostgreSQL5432, UAC, native service restart or launcher activation.

## Next gates

1. Resolve stale source pins and collect explicit per-producer admission decisions.
2. Independently review any new executable code; record immutable SHA and purpose.
3. Build a **new disposable generation** with only approved runtime files; preserve the original approved manifest until the replacement has been reviewed.
4. Run security negatives, PostgreSQL14/18, FastAPI/Chromium 13TF, F-G-D and Python/Platform against the **same** candidate SHA.
5. Release remains **NO_GO** until every required gate passes.
