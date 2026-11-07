# Approved v1 integration and ten-instrument acceptance plan

Normative requirement: Issue #8. Priority: correctness → completeness → recovery → analytics → visualization → performance. No main merge. Physical tests are authorized only within Issue27's owned current-PC scope; the cancelled UAC must not be retried automatically.

## Next bounded financial provider gate, 2026-11-07

The immutable physical candidate remains0ff59c69. B73766's future-horizon correction now has independent six-case PostgreSQL acceptance in a separate retained fixture database, in addition to13pure checks. Ebec563's diagnostic-code correction has independent48HTTP checks. Neither change is composed or activated yet. A628 catalog metadata and C3cb binding remain the reviewed producer inputs; hosted CI remains a separate pending gate.

1. B owns a same-snapshot, bounded knowledge-time port for C's required `quality_available_at_ms`. Issue2 comment6711027252 records missing observation/source/run/raw knowledge timestamps in the current bounded projection. Preserve original timestamps; do not substitute event time, source fetched_at, client cutoff or current read time. A conservative provable upper bound may reject older cutoffs. Historical as-of reconstruction is not implied. Legacy/missing proof stays unavailable. Test backdating, late observation/run links, corruption/resolution chronology and sub-ms database timing. MASTER reviews the exact published SHA with actual retained-DB checks before adoption.
2. MASTER owns the B→shared-v1→C→E adapter; producers keep their own modules. First implementation must use one bounded `read_page` snapshot and its original observation/run/proof documents. Do not call a separate legacy resolver after the snapshot or combine independent page snapshots into a fictitious consistent read. Initially reject input beyond one exhausted bounded page; output limits must never truncate input trades and silently certify a bar. Resource exhaustion, malformed provenance, unsupported resources and unresolved cutoffs receive safe explicit reasons.
3. C3cb remains the sole arithmetic implementation. Supply explicit market/range, information cutoff, supported UTC interval, decimal step, original source links and proved quality-known time to `bind_storage_trades`; use its existing `response` path. Do not duplicate aggregation in E or downgrade missing taker side into Bid/Ask certainty. No source precision or `COMPLETE` may be invented. Partial/unverified live history remains partial/unverified even if calculations are possible.
4. Ebec563's frozen financial request is the input contract. Explicit price steps work without fabricated instrument increments; `instrument_tick` requires an effective, provenance-bearing metadata reference known by cutoff, otherwise unavailable. A owns source increments and catalog pagination; B owns their canonical storage/provenance. No request or factory performs ingestion, migrations or service installation. Default physical provider/config remains unchanged until an independently tested candidate is deliberately selected.
5. D consumes actual typed responses and unavailable reasons. MASTER must test real API→browser paths and screenshots, then the bounded ten-instrument scenario; existing fixture/static tests are not live financial UI acceptance. F remains a separate simulation stage; G canonical PostgreSQL integration and Trader Intelligence source permissions remain separate gates. No foreign Gateway, real orders or new trader collection.

Required regressions before activating the adapter: same-ms/ns ordering, original fetched-at cutoff, late quality knowledge, missing/undeclared/mismatched run, corrupt/unavailable range, future horizon, non-exhausted input, resource/byte limits, explicit-step versus unavailable tick reference, unsupported dataset, canonical output schema, honest reasons and absence of private diagnostics. Keep all physical fixture data/backups, and do not repeat the completed restore operation.

## Commit-by-commit sequence

1. Publish approved schemas/semantic validator and independent fixture gates. Record each branch head and reject incompatible published heads immediately; do not wait for all agents.
2. A publishes source adapters and persisted pagination evidence. Verify original sub-ms ordering, no fabricated precision, REST cursor exhaustion, empty/repeated pages, corrupt/revised archives and checkpoint restart. Fixture tests first; live endpoints separately.
3. B publishes versioned PostgreSQL migrations and idempotent storage/read APIs. Check full stable identity, duplicate content versus conflicting content, synthetic-key collision policy, immutable raw provenance and availability/coverage propagation. Execute upgrade twice on a disposable database, never on a user database. Match B Store.read keyset cursor with E.
4. C consumes B-verified records only. Check deduplication, exact decimal sums under input permutations, sub-ms ordering, UTC D/W/M buckets, missing side, taker labels, partial CVD and official/derived comparison context. Publish actual callable paths, output schemas and units.
5. E binds A/B/C providers and shared validators, retaining API schema_version=1.0/data/next_cursor with explicit metadata mapping. Contract tests validate every returned row; E contains no duplicate analytics implementation. No provider yields DATA_UNAVAILABLE with reason, not empty success.
6. D integrates E's bounded endpoints. Browser tests cover multi-selection across filters, unavailable/partial/unverified reasons, synchronized ranges, unknown denominator, error/retry/pause/resume and restart. Missing metric panels remain visible with reasons.
7. Integrate exact reviewed SHA from each corrected Draft PR into integration/master sequentially; run full suite and schema/consumer gates after every commit. Keep rejected minimal initial heads out.

## Ten Bybit instrument scenario

The ten-instrument selection is obtained from the LIVE public catalog, sorted deterministically by `(category,symbol)`, with catalog timestamp and full pagination evidence. Include available spot/linear/inverse market coverage where feasible; if a source/market cannot supply ten chosen histories, report that fact, never replace live results with fixtures. Preserve the selected manifest in test artifacts. Instrument count, market identity, archive availability and launch boundaries must be recorded before starting downloads.

Use bounded date ranges and an explicit small archive-byte budget for the first live test. For each instrument record: discovery source, selected range, request/cursor/count/terminal evidence, exact timestamps, source hashes, ingestion run, canonical persisted rows, coverage and availability. For perpetuals query documented OI/funding sources; spot marks unsupported datasets UNAVAILABLE with reason.

For every selected identity: ingest once; interrupt after a persisted checkpoint; restart and recover; retry identical data and prove no duplicate stable IDs; introduce synthetic changed-ID content only in an isolated fixture run and prove a source conflict cannot silently change provenance. Compare DB row counts/hashes, checkpoint progress and official/derived analytics with documented units/tolerance. Do not send trades/orders.

UI must show all ten instruments, per-source progress, real coverage/quality and explicit unavailable panels. A successful HTTP response or 100% task counter does not establish historical completeness. Validate raw→verified→normalized→aggregated→visualized provenance inheritance across the entire pipeline.

## Fixtures versus live evidence

Synthetic fixtures cover sub-ms/ns and coarse ms sources, duplicate/synthetic ID collisions, partial/corrupt archives, empty pages, repeated cursors, late/unclosed candles, source conflicts, missing taker side, exact arithmetic and restart. They do not prove Bybit retention/availability or live end-to-end readiness.

Initial live probes were DNS-blocked; after renewed network permission evidence/public-probes.json records successful catalog/archive checks, and evidence/live-baseline-postgres.json records ten live candle identities plus XRP43210 archive rows persisted/retried/clustered in a disposable database. These checks target legacy baseline integrity, not yet B's tester_storage migrations or full A–E integration. No physical Windows acceptance is inferred from running Python tests on this host.

## Final gates

Require all A–E independent conformance suites green for the exact integrated SHAs; all CI jobs completed successfully for the candidate SHA; real PostgreSQL migrations/recovery; live ten-instrument artifacts; automated UI evidence; no critical defects. Otherwise NOT_READY. Append-only provenance and actual process-kill recovery require explicit evidence, not mock-only assertions.

