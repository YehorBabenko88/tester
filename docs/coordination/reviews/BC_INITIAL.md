# Independent review: B integrity and C analytics

Repository only: YehorBabenko88/tester. Reviewed B at 801297fb3f7e20aba5341f2aae3a5c58074b704b, C at 0070b5fcb9180a79b213698c99ea289df4442383; contextual baseline d996b8669e3e85f559fe4adba768bad6d994c9c5. No remote mutations or product changes.

## Branch scope and provenance

GitHub compare for each SHA against frozen main returned diverged: ahead_by=1, behind_by=14, merge base d36aa458c3efad876a15b0dd345079f0ff20ae0f. B adds only src/quality/candles.py; C adds only src/analytics/footprint.py. Recursive Git tree responses were not truncated. Neither branch contains automated tests, storage/migration implementation or provenance documentation. Both branch trees contain only README.md, bybit_history.py, requirements.txt and the respective new module. Therefore the missing baseline files are branch age, not deletions in the workstream commits; do not replace main wholesale with either snapshot.

Issue #2 requires schema/migrations, append-only raw provenance, idempotent upserts, range ledger, checksum verification, gaps/backfill/retry. Issue #3 requires deterministic trades-to-bars, taker delta, CVD, footprint, OI/funding alignment and true versus inferred labeling. These one-file implementations do not satisfy either issue end to end.

## Actionable findings in branch additions

1. **P1 B accepts invalid financial data as complete (candles.py lines 22-25).** OHLC values are converted to binary float; NaN comparisons all evaluate false. Executed `inspect([[0,'NaN','NaN','NaN','NaN','NaN']], '1', 200000)` returns complete=True and no errors. The valid-number precision boundary also matters: open/close=9007199254740993 and high/low=9007199254740992 are invalid exact OHLC, but inspect marks complete because float collapses them. Negative OHLC is accepted too. Use finite exact decimal validation and domain checks before certifying data.

2. **P1 B completeness cannot establish requested coverage (candles.py lines 6, 26-29).** The function accepts no requested start/end, checks only interior gaps and returns complete=True for an empty list (executed). A truncated one-row historical response also has no gap. Persisting this result as a range-complete ledger would suppress backfill of missing leading/trailing history. Require explicit expected closed range/calendar and distinguish empty/unknown from verified complete.

3. **P1 C has no market/identity boundary or duplicate protection (footprint.py lines 5, 10-21).** All input trades share the same `(bar, level)` map regardless of symbol/category/trade ID. Executed duplicate input with trade_id=x twice returns buy=2 and trades=2 for one real buy of size 1. Combined symbols at the same price/time would likewise silently merge. Either enforce and document a deduplicated, single-market input contract at a tested caller or include source identity and deduplication here. No caller exists in either reviewed branch to enforce it.

4. **P2 C decimal context loses exact quantity and can make results input-order dependent (footprint.py lines 18-20).** Decimal operations use ambient default precision 28. Inputs with buy quantities 10000000000000000000000000000, 6, 6 expose rounding; sorting output does not make accumulation deterministic. Financial exactness requires explicit precision policy or exact scaled integer arithmetic, including price/tick division.

5. **P1 C omits required analytic products (footprint.py lines 22-27 and complete tree).** Output contains footprint rows and local level delta only; no CVD, OHLC trade bars, OI/funding alignment or source/quality labels. Module docstring advertises CVD without implementing it. There are no alternate analytics files in the reviewed tree. Implement and test these before closing #3.

6. **P2 C accepts invalid execution records (footprint.py lines 12-16).** Quantity zero and negative prices are accepted, and nonfinite price is not explicitly rejected. Executed size=-0 creates a zero-volume row and increments trades. Failed ingestion must not manufacture counts from invalid executions. Require finite positive price/quantity and validated timestamp units.

## Baseline integration risks, not introduced by these commits

These are static observations in the frozen main files requested for context. B does not repair them; do not attribute them to its one added module.

- **P1 incomplete archive rows feed footprint:** engine.py 189-196 commits each 1500-row batch before gzip EOF/CRC and total count checks (197-200). An invalid/corrupt later row leaves earlier trades committed; build_clusters at 227-232 aggregates all trades with no successful trade_files/coverage join. Failed imports can therefore present partial volume as ordinary footprint. Keep staging separate or filter verified generations, then atomically publish.
- **P1 correction/recovery silently retains old data:** engine.py 190-195 uses ON CONFLICT DO NOTHING, then verifies only row count, not existing row content. A same-ID/same-count revised archive can produce a new checksum in trade_files at 201-205 while existing price/size/raw remain from the old file. If revised archives remove IDs, surplus stored rows cause perpetual count mismatch with no repair path. Need generation-aware staging and exact reconciliation.
- **P1 provenance is overwritten:** trade_files primary key is category/symbol/day (db.py 35-37), replaced on conflict (engine.py 203-205); local archive is replaced at 206. Candle and observation upserts overwrite old values/raw (db.py 72-84). This does not implement append-only provenance or retain historical source payloads for audit. Coverage is committed separately from payloads (db.py 57-63), so publication has multiple crash windows; retries help some but do not establish atomic provenance.
- **P2 database identity/migrations:** db.py 17-49 validates only a database name and uses unqualified CREATE TABLE IF NOT EXISTS. There is no schema version/shape verification or dedicated schema/search_path enforcement. Existing incompatible same-name tables can survive initialization and later positional INSERT statements fail or target unintended shapes. PK category/symbol/kind/timeframe/time does separate candle identities; raw trade identity additionally includes day, so cross-day repeated IDs are not globally rejected. Whether that is correct requires a documented exchange-ID scope.
- **P2 partial last footprint bucket:** db.py 115-121 limits individual `(bucket,price)` rows to 700, which may cut a bucket's levels partway. A caller displaying complete footprint bars must select complete buckets and then all their levels or explicitly mark truncation.

## Verification performed and limits

Executed Python 3.14 against exact fetched copies of both added modules saved as work/review_b_source.py and work/review_c_source.py. Ran direct probes for empty coverage, NaN, float precision collapse, negative OHLC, duplicate trade input, zero quantity and decimal quantity accumulation/order. Reviewed GitHub issue #2/#3 bodies, compare metadata and full nontruncated recursive trees. Static review of frozen db.py, engine.py and test_core.py: seven core tests cover boundary/symbol/API cases but not these integrity, recovery or analytics paths. No PostgreSQL server, live exchange request, archive repair or branch-owned automated suite was executed. Existing numeric SQL storage avoids binary float conversion for stored prices/quantities; the explicit float bug above is confined to the new B validator.

Sources: https://github.com/YehorBabenko88/tester/issues/2 ; https://github.com/YehorBabenko88/tester/issues/3 ; immutable code links https://github.com/YehorBabenko88/tester/blob/801297fb3f7e20aba5341f2aae3a5c58074b704b/src/quality/candles.py and https://github.com/YehorBabenko88/tester/blob/0070b5fcb9180a79b213698c99ea289df4442383/src/analytics/footprint.py ; baseline https://github.com/YehorBabenko88/tester/tree/d996b8669e3e85f559fe4adba768bad6d994c9c5 .

