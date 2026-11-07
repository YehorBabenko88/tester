# Price-only candles v1.1 — explicit optional adoption

Approved core v1.0 remains unchanged. This additive version addresses a producer incompatibility documented in Issue7 comment6708328264: mark/index/premium source candles are price observations, not trade OHLCV. It is not a silent loosening of v1.0 trade-candle validation or an assertion that E endpoints already accept1.1.

## Representation

Use schema_version="1.1" with kind official_mark/official_index/official_premium and the existing identity, UTC timestamps, provenance, availability and separate coverage structures. OHLC remains exact decimal strings. Mark/index prices are positive; premium values may be signed including zero. Volume must be absent or null; numeric/string0 is rejected because it would imply observed zero trade volume. Negative mark/index cannot masquerade as premium.

Fixed intervals require their exact UTC grid and duration; weeks anchor Monday UTC. Calendar M starts at UTC month boundary and ends at the next month boundary (including leap years). timestamp_ms equals start_ms; coverage contains the interval; OHLC, quality and closure rules remain enforced. NaN/Inf/floats/exponents are rejected. Sub-ms trade handling is untouched.

Producers retain original five-field response arrays and genuine source metadata. No source hash/run or completeness is fabricated. Call contracts.v1_1.price_candle.validate_price_candle explicitly. v1.0 validators continue rejecting these1.1 records. Consumers must opt in through an agreed price-source resource/negotiation and publish round-trip fixtures; don't mix versions into an endpoint advertising only1.0. Until E/D adoption is verified, expose DATA_UNAVAILABLE with the contract-version reason.

## Evidence and source limits

Official Bybit response documentation lists timestamp + OHLC (five fields) without volume for [mark](https://bybit-exchange.github.io/docs/v5/market/mark-kline), [index](https://bybit-exchange.github.io/docs/v5/market/index-kline) and [premium index](https://bybit-exchange.github.io/docs/v5/market/premium-index-kline). Signed premium support preserves producer-reported valid values; it is not a claim that every live sample is negative.

Eight independent fixture methods cover signed premium, no invented volume, invalid numbers/bounds, v1.0 immutability, coarse/fixed/month/week boundaries and unclosed/quality guards. Independent review found off-grid/wrong-duration acceptance; a rejecting regression was observed RED before the fix and GREEN afterward. Six real bounded BTCUSDT price-source rows (two from each endpoint) also validated, all UNVERIFIED/UNKNOWN. See docs/coordination/evidence/price-candle-v11-live.json. Neither these fixtures nor six live rows prove historical completeness, A–E integration or deployment readiness.

Price-level/analytics metrics requested by C remain a separate future contract review; this version does not grant arbitrary extra fields or duplicate C calculations.
