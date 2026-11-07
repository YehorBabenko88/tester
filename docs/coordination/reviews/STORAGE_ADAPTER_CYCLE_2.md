# Independent storage adapter review, cycle 2

Reviewed root candidate work/master-supervisor/src/integration/storage_api.py and test_storage_api_adapter.py against frozen B Store.read at 5154efab05d44b96f4aa83819672396d75679f2e and current shared contracts/v1 validator. No product edits.

## Confirmed findings

**P2: empty pages erase known integrity failure.** read_trades first computes _quality, which correctly turns report.corrupt/unavailable into availability UNAVAILABLE and coverage ERROR and preserves report.reason. It then unconditionally overwrites that metadata for any empty page: generic no-records reason and UNKNOWN coverage. Executed identical corrupt report with reason `sha256 mismatch source archive`: nonempty result retains that reason and ERROR; empty result loses both, returning generic reason and UNKNOWN. This hides a known checksum failure exactly when an API consumer needs to distinguish acquisition absence from damaged history. Preserve existing ERROR/UNAVAILABLE reason when data is empty; prevent only optimistic COMPLETE/AVAILABLE, and test both corrupt and explicitly unavailable empty ranges. Reproduction output: work/storage-adapter-cycle2-probes.log.

**P1 boundary validation gaps (already being repaired by root).** Executed out-of-range timestamp, unsorted `[z,a]` and duplicate `[a,a]` with limit=1 against injected Store returned accepted responses. Unsorted overfetch emits cursor z and can permanently drop a on subsequent keyset calls; duplicate overfetch can produce phantom continuation. Validate every storage row including overfetch, require exact integer in-range timestamps and strictly increasing `(timestamp_ms,record_key)` after incoming cursor, before slicing. Root explicitly owns these fixes; this report records pre-fix behavior, not a claim the eventual updated candidate still fails.

## Passing and correctly conservative behavior

- Eight adapter tests executed and passed. Exact decimal strings are projected without a float round trip; no internal SQL extras leak into shared record shape.
- Genuine source microseconds produce exact ns strings ending in 000, with shared precision still us. Missing source microseconds fails closed. Ordinary s/ms sources retain null ns; supplied ns must be an exact integer.
- Composite B identity maps to shared synthetic with explicit identity_method; shared validator rejects unsupported COMPLETE on synthetic identity. This is appropriately conservative.
- Cross-instrument continuation cursor is rejected. Same-ms keyset pages preserve all three fixture identities in the ordinary sorted case.
- Current B Store.read alone cannot supply authentic ingestion-run provenance. Requiring an external provenance_resolver, and raising IntegrationUnavailable when it returns no sources, is correct. Do not invent read-time UUIDs, source kind or claimed run linkage. An authenticated resolver backed by new B source→run relation is still an integration dependency.
- B's bare complete boolean without evidence_refs becomes UNVERIFIED/UNKNOWN, not AVAILABLE/COMPLETE. With actual B report this keeps metadata conservative. Availability/coverage are separate shared fields.
- B numeric columns/extra source fields are discarded in favor of exact canonical payload price/size/side. Rename size→quantity and side→taker_side conforms to E's public schema. No C analytics are introduced.

## Verification scope

Ran `work/venv/Scripts/python.exe -m unittest discover -s work/master-supervisor -p test_storage_api_adapter.py -v`: 8 tests, all passed. Ran direct adversarial read_trades/project_trade calls using synthetic Store rows; output saved work/storage-adapter-cycle2-probes.log. These are adapter unit probes, not live PostgreSQL API requests. B's separate actual PG suite was successfully rerun by root: work/b-cycle2-pg-root.log records 40/40 tests including migrations, concurrent writer, kill before/after commit and SQL backup/restore. Added that acknowledgement to work/review-b-cycle2.md; independent agent's previous TCP denial is superseded for those database tests.

