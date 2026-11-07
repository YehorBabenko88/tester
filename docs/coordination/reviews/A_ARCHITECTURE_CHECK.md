# A architecture integration check

Exact PR10 head: **`39645a64ed424270401de3b1f35767b1fd7c3d74`**. Standalone tester only, prior to Trader Intelligence. No product/remote changes or cross-repository dependency. Commands/results: `work/a-architecture-check-review.log`.

## Gate result

**Prior missing-export/provenance/pagination/precision blockers are resolved on the independently exercised paths.** Current sources include acquisition runs and task attempts; raw response/evidence versions; original cursor in/out and response counts; separate canonical publication counts; source-kind/ingestion-run/availability/coverage links; exact shared and B projections; and explicit conflict quarantine. They no longer export unsafe row-only JSONL.

Executed **40 A unit tests pass**, plus **4 pinned shared/B consumer tests pass on B5154** and **4 pass on B47fd**. No new P0 failure found in those gates. One remaining P2 data-quality defect below should be tracked before broad source mixing; these checks do not constitute complete A–G/real PostgreSQL integration approval.

## Material remaining issue

**P2 — Decimal formatting alone produces false identity conflict and removes valid trades from publication.** `src/ingest/handoff.py:42-43` semantic_hash hashes original payload strings; `build_bundle` (`157`) marks any differing hash for the stable key as conflict, then excludes quarantined records (`179`). Thus numeric-equivalent source variants such as price `1.0`/`1.00` and size `2.00`/`2.0` for the same original exchange trade ID/time/side become competing semantic versions.

Executed scratch-only `review_probe.py` using two tiny real gzip/CSV fixtures and the actual current adapter, Journal and pinned B47 `normalize`. Both normalized records have **equal B content_hash**, while A emits two quarantined source versions, one conflict and **zero storage batches**. No true price/size/time/side changed. Preserve both raw source versions/provenance but compare a typed canonical semantic projection for conflict decisions; retain distinct nonfinancial source columns as evidence rather than identity-changing economics. Add decimal-scale (including plain/exponent source equivalents where supported), optional-field and timestamp-encoding equivalence fixtures alongside actual price/size/side conflict rejection. This is conservative loss of usable observations, not false COMPLETE promotion or silent raw-byte loss.

## Current corrections verified

- Bundle format deliberately carries runs/tasks/evidence/raw_record_versions/records/storage_batches/conflicts/artifacts. References are keyed and resolvable; actual acquisition proof is required for official projection, legacy bookkeeping stays UNKNOWN/UNVERIFIED. Unknown/invalid rows remain raw with explicit projection_errors.
- REST response evidence is persisted before filtering/deduplication. Actual nextPageCursor/absence, raw result, original HTTP body/hash where available, request bounds and terminal facts survive. Failed request count stays null, not fabricated zero.
- Local publication pages are explicitly labeled local_publication_batch with unique_published_rows and UNVERIFIED; source_response_count remains separate. Duplicate fixture proved raw response2 versus unique publication1; no synthetic exchange cursor, available_range or COMPLETE is claimed.
- Actual pinned consumers validate shared trade/candle/observation/pagination records and B normalize/validate_evidence. Dataset names and array→named-field projections now work. Original decimal strings and exchange IDs survive. Seconds/ms do not invent finer precision; us preserves exact Btimestamp_us and shared ns-unit representation with us provenance; ns preserves actual source precision.
- Signed/volume-less reference prices are preserved in B/raw and explicitly fail immutable shared v1.0 projection. No fabricated volume or silent v1.1 adoption. The separate optional shared v1.1 adoption remains an explicit integration decision.
- Archive bytes are hash-checked and included as artifacts; unavailable/changed files are labeled UNVERIFIED without fake bytes. Exports include transitive acquisition manifests for B persistence. Export materialization is in memory and explicitly requires bounded work units, not whole-exchange snapshots.

## Provenance and execution limits

Local Git object was absent; read-only `git fetch --no-write-fetch-head` failed DNS/getaddrinfo. Therefore a Git archive could not be produced locally. Used the GitHub connector recursive tree/fetch_file **pinned to the exact SHA** into isolated `work/a-architecture-check`. Independently compared all **13 source/test hashes** in author's verification manifest against local bytes; all matched. No branch checkout or source modification occurred.

The two consumer runs used the actual pinned shared validator `9766c0093e2608280875ac56db074414a3d62c69` and B baseline `5154efab05d44b96f4aa83819672396d75679f2e` / run-ledger `47fd36f32389174377682a13862f5b36c7a886da`, read via existing root Git objects into isolated references directories. Workspace temporary files were configured; root venv supplied jsonschema/psycopg. These tests validate functions and fixture packages; **no PostgreSQL connection was opened**.

Author's `handoff-verification.json` reports **2696** live strict/B records (2693 archive plus3 REST), PostgreSQL18.4 retry/read checks, empty-window evidence and stopped isolated cluster. This is **inspected author-supplied evidence only**. This review did not download large archives, repeat liveHTTP, replay those2696 records or verify the author's scratch database. Root replay remains required before representing that integration result as independent.

