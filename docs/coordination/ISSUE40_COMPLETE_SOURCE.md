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

The existing service continues atomic catalog-plus-ticker publication: ticker failure
retains the prior snapshot and exposes the failed stage and completed catalog scope
evidence. Independent ticker scheduling and historical metric acquisition remain
integration work; cursor exhaustion does not assert ticker metric coverage.

References: https://bybit-exchange.github.io/docs/v5/market/instrument and
https://bybit-exchange.github.io/docs/v5/enum (read 2026-11-08).
