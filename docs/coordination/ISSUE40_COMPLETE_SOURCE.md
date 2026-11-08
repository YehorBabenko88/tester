# Issue 40 source acquisition

The catalog source exhausts separate linear/inverse queries across Bybit's documented
Trading, PreLaunch, PendingOpen, Delivering and Closed status filters. Counts are
observations, not configured totals. Global page/row/body budgets, repeated cursors,
duplicate symbols, malformed responses, cancellation and rate limits fail explicitly.
Raw returned status, contract type, settle coin, category, URL, response timestamp and
local observation time are retained. `contract_kind` is perpetual, dated or unknown.
Historical delisting coverage remains UNKNOWN even when current endpoint cursors are
EXHAUSTED. The exchange does not promise a transactionally fixed universe across pages.

Real verified public requests on 2026-11-08 found 930 Trading linear instruments
(890 perpetual, 40 dated) and 26 Trading inverse instruments (22 perpetual, 4 dated).
All status scopes yielded 1995 linear and 76 inverse unique instruments. An independent
curl/Schannel enumeration used limit 500, proving real cursor continuation: two pages
for linear Trading and three for linear Closed. Production Python limit 1000 yielded
identical normalized identity SHA256 values for all ten category/status scopes about
two minutes later. See the timestamped JSON reports in `docs/evidence/issue40/`.
These are observations from that time, not expected permanent totals.

Two live quirks are covered by regression tests: an empty status scope returns blank
category with an empty list and terminal cursor; the Closed linear filter returned
five PendingOpen instruments alongside 1053 Closed instruments. Actual returned status
is preserved, with observed status counts in each requested scope's coverage.

Python requests' certifi-only default lacked the local issuer. The source uses an
HTTPS adapter with `ssl.create_default_context()` to load operating system trust,
while retaining certificate and hostname verification. An explicit verified CA bundle
path can be supplied by the isolated runtime; disabling verification is rejected.
No trust store, service, database, native installation or trading endpoint is modified.

Catalog and ticker acquisition now have independent failure semantics. An exhausted
catalog publishes even when tickers fail. Matching prior metric values retain their
original source/observation times and become STALE; new instruments have UNAVAILABLE
metrics. Removed instruments disappear from the new catalog. A failed ticker attempt
returns false for scheduler backoff while publishing the new catalog revision.
Catalog and tickers expose separate freshness, provenance, last-success and error
metadata; query qualifies each metric using that metric's own source timestamp.
Cancellation still fences every publication. Independent observation history remains
integration work; cursor exhaustion does not assert ticker metric coverage.

Units are explicit and derived from instrument base/quote metadata, never symbol
suffix parsing. Price is quote/base, 24h change is a ratio. Linear volume/OI use base
coin and turnover uses quote coin; inverse volume/OI use quote coin and turnover uses
base coin. Unknown currency metadata yields UNKNOWN units. Official references are
the instrument schema, ticker fields, [Kline units](https://bybit-exchange.github.io/docs/v5/market/kline),
and [OI units](https://bybit-exchange.github.io/docs/v5/market/open-interest).
The Kline volume/turnover dimension conventions are applied to the corresponding
24h ticker aggregates. The ticker's documented price-change field is confirmed as a
fraction by comparing actual lastPrice/prevPrice24h arithmetic in the additional
timestamped unit-ratio evidence file. Values remain in source units without implicit
percentage multiplication or cross-currency aggregation.

References: https://bybit-exchange.github.io/docs/v5/market/instrument and
https://bybit-exchange.github.io/docs/v5/enum (read 2026-11-08).
