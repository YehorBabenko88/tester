# PR10 handoff correction

Base: addbdda232bc1a604a7a637bdad479752c4cccfe. Read-only reference contracts 9766c0093e2608280875ac56db074414a3d62c69 and storage 5154efa plus new run ledger 47fd36f. Only A-owned ingestion/tests/docs change.

1. Reproduce export metadata/evidence loss; add fixtures for strict shared trade/candle/observation projection and B normalize/evidence consumption.
2. Append-only SQLite runs, acquisition evidence, task attempts and raw record versions; keep original source responses/counts and distinguish conflicts from provenance-only repeats. Restart cannot discard failed/paused attempts.
3. Record actual REST input/output cursors, terminal reason, raw source precision and run refs at request time. Cached legacy tasks lack proof and must not become complete.
4. Export one atomic self-contained JSON handoff (replaces the unsafe row-only JSONL export): raw versions, linked runs/tasks/evidence, explicit availability/coverage, B named-payload projection and strict shared projection. Never invent an exchange cursor to satisfy B's publication-batch protocol. B publication evidence remains UNVERIFIED and keeps source response counts separately.
5. Run A tests, pinned shared validator/B consumer fixtures, fresh small public smoke and restart. Independently review changes, push only workstream/a-ingest and report exact HEAD in PR10/#1/#7. No merge.

Schema gaps must be reported rather than repaired with fake values: shared candle currently requires positive OHLC and volume even for signed premium/volume-less reference-price candles. Raw/B records must survive any strict-projection rejection.

Completion ledger: 40 A unit tests; four pinned shared/B consumer fixtures separately passed for each B5154 and B47. Real source2693 trades+3 closed candles validated under the shared contract and B; PostgreSQL18.4 ingest/read/retry succeeded with caller-supplied actual run provenance under B47, plus real prelaunch empty-response observation ledger. All coverage remains PARTIAL/UNKNOWN and B verified=false. Independent review found/fixed nonfinite Decimal export failures and second-window failure bounds; each has a RED→GREEN regression. Final focused review found no remaining blocker. Shared signed/volume-less candle projection remains explicitly unsupported and reported in Issue #7. No merge/deployment.
