# Current A Draft PR #10 review

Reviewed exact current head `addbdda232bc1a604a7a637bdad479752c4cccfe` from [Draft PR #10](https://github.com/YehorBabenko88/tester/pull/10), against approved [Issue #8](https://github.com/YehorBabenko88/tester/issues/8). This is a new implementation, not the original `43d0126` helper. The old A conformance failures are historical evidence and must not be reported as this head's results.

## Current findings

1. **P1 — Exported records have no approved availability/coverage contract.** `src/ingest/rest.py:100` and `src/ingest/archive.py:116` emit versioned source records with no availability value from AVAILABLE/PARTIAL/UNAVAILABLE/UNVERIFIED and no separate coverage context/reference. REST run/window status is lower-case `observed`/`unverified` (`rest.py:138-147`), while an archive's file-validation status proves gzip/CSV integrity, not trade completeness. README correctly qualifies `observed`; that qualification is not available in each exported record. A consumer cannot safely inherit the explicit acquisition status required by Issue #8. Add a separate availability/coverage envelope or stable reference before connecting these records to B/C/E. Test partial archive, unverified funding and late/open candle propagation through actual JSONL export.

2. **P1 — Missing ingestion-run and source-kind provenance prevents the approved audit linkage.** Actual REST source context at `rest.py:100` contains URL, fetched_at and row hash; archive source context at `archive.py:103,116` inherits optional caller metadata plus file hash/layout and fetched_at. Neither adapter requires or generates an ingestion run ID or an explicit source kind. A caller may supply an archive URL only and obtain an apparently valid v1.0 record; the executed probe did exactly that. The required precision field itself does exist and works. Generate/require a run identity and source-kind discriminator on actual export, and ensure every row links to its run's evidence. Test rejection of incomplete provenance and preservation across retry/restart rather than relying on optional arbitrary caller dictionary keys.

3. **P1 — Pagination evidence exists locally but is lost at the integration export.** `rest.py:93` now correctly records request params, input cursor, bounds, response count and status, and `rest.py:139-140` journals the window evidence. However `journal.py:28-30` exports only record bodies; these have no task key, window ID, run ID, terminal condition, availability or coverage reference (`rest.py:100`). Exporting the supported JSONL therefore drops the evidence B/E would need to distinguish a partial/repaired acquisition from a terminal, verified acquisition. Preserve a referenced task/run manifest in the export contract (or export records plus validated window manifests together). Add a test that consumes only exported artifacts and can recover exact request boundaries, terminal reason and incomplete status. This finding does not assert that the current paginator lacks request evidence: it has it, but the external boundary does not carry it.

These are integration-contract blockers, not a claim that this implementation cannot download or paginate. PostgreSQL persistence is expressly delegated to B; using SQLite here as staging is consistent with Issue #8. Cross-process locking is explicitly excluded by the PR and was not treated as an undisclosed claim.

## Verified improvements at this exact head

- `archive.py:21-29,92-116` provides real `timestamp_ms`, nullable `timestamp_ns`, precision, exchange ID and original decimal-string payload adapters. Executed a genuine source timestamp `1700000000.123456789`: received exactly `1700000000123` ms and `1700000000123456789` ns; price `123.4500` stayed a string. A millisecond-only input produced no invented ns value. The original missing timestamp-adapter finding is resolved for this source case.
- Download validation/cache recovery and strong-ETag Range probing are implemented (`archive.py:121+`), with direct regression tests. The original plain-body download implementation is gone.
- REST now emits concrete request evidence, applies endpoint limits, supports cursor/descending-time pagination, re-probes empty windows, repairs cadence gaps, uses server time for candle closure, and preserves staged repairs. Tests exercise these behaviors.
- Published `docs/ingest/verification.json` is timestamped 2026-11-07T13:33:56Z, reports 27 tests, six archive samples and fourteen live REST cases, and explicitly records later DNS failure. `boundary-evidence.json` is timestamped 13:22:59Z, labels earliest monthly observations and nominal archive dates without a global completeness claim. These are inspected author-supplied evidence, not independently repeated live network tests.

## Execution

Exact files fetched read-only via GitHub connector into isolated scratch `work/a-latest`; no remote branch or product changes made for this review.

Initial `../venv/Scripts/python.exe -m unittest discover -v` ran 27 methods but had 19 environment errors because sandbox default temporary directories denied writes. Without modifying the pinned tests, reran with Python `tempfile.tempdir=os.getcwd()` and unittest discovery: **27 tests pass**, exit 0. No skipped tests.

Executed `../venv/Scripts/python.exe review_probes.py`: exit 0. It exercised real archive gzip/CSV parsing, exact source timestamps/decimals, actual Journal SQLite staging and a mock REST client. It produced one observed closed candle with request bounds `[0,60000)`, returned count 1 and EXHAUSTED evidence, plus its exported-record-shaped body. That body confirms missing run/source-kind/availability/coverage linkage described above.

No live HTTP or real PostgreSQL tests were run for this branch review. The coordinator's real-PG tests apply to its separate engine fix, not this PR. No review comment, PR mutation, commit or merge was made.

