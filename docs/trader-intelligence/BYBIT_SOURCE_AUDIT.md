# S0 — Bybit trader-source availability audit

Checked **2026-11-07, completed 15:01:55 UTC**. Read tester [Issue #25](https://github.com/YehorBabenko88/tester/issues/25) and [architecture correction on PR #11](https://github.com/YehorBabenko88/tester/pull/11#issuecomment-6708565655). This is documentation/source research for standalone tester, not permission to deploy an adapter. Existing A–G conformance stays first; Trader Intelligence remains separate. No dependencies on other repositories or their Gateway.

**Result:** anonymous public market executions are documented; account-specific history and user exports are documented; some Master Trader disclosures are described, but an anonymous, permitted, documented machine-readable third-party history API was **not established**. Do not promise a named-trader historical feed, full account reconstruction or unrestricted reuse.

## Source matrix

| Source | Identity / access | What is established | Window/fullness / lag | S0 gate |
|---|---|---|---|---|
| V5 recent public executions | Anonymous market prints; no trader UID/profile field documented | price, size, taker side, execution ID, ms time | Recent snapshot, not a named-account ledger | Documented market context only; runtime access unverified in this audit |
| V5 account execution/order history | Authenticated account scope, not arbitrary Master alias lookup | Own executions/orders and private execution details | Endpoint/account-mode retention and pagination apply | Not called; blocked in this no-keys task |
| Classic Master profile disclosure | Profile/visibility-dependent, pseudonymous | Help describes current/past trades and performance | Mixed update cadence; full historical retention unknown | No permitted automated profile ingestion established |
| Master User Center | Account-owner view | Own positions, fees, order details, followers | Distinct private account perspective | Not public evidence; no login/access attempted |
| User-generated CSV/account export | Owner-authorized file | Official export workflow and account data types | Generated later, product/time filters; contradictory retention statements | Conditional offline import only after actual user supplies/authorizes file |
| Copy Trading Pro / TradFi | Separate products and eligibility | Own product rules/metrics, not interchangeable Classic executions | Product-specific cadence | Separate registry entries; no universal copy-trade feed |

## 1. Anonymous market data cannot identify a trader

[Recent public trades](https://bybit-exchange.github.io/docs/v5/market/recent-trade) documents `GET /v5/market/recent-trade`. Request scope is category/symbol, not trader. Limits are spot 1–60 and other categories 1–1000; response fields include execId, symbol, price, size, taker side and time, but no account identifier. Its archive link does not create an identity mapping. Never infer that a print belongs to a Master because price/quantity/time resembles a disclosed position.

One small unauthenticated GET was attempted through the browser fetch tool with `category=linear&symbol=BTCUSDT&limit=1`; the tool reported the URL inaccessible and returned **no response body or HTTP status**. Thus this audit confirms the published interface, not successful live access or a 403/empty market result. No retries, alternate hosts, proxying or bypass.

## 2. Account history is authenticated, not public trader lookup

[Execution history](https://bybit-exchange.github.io/docs/v5/order/execution) describes users' execution records and cursor pagination, with <=7-day request spans, default seven days and page size <=100. Its navigation labels two-year history; do not assume that applies uniformly to every account generation/product. Same-time executions need a documented tie-break; multiple fills can belong to one order. No parameter selects an arbitrary public trader alias.

[Order history](https://bybit-exchange.github.io/docs/v5/order/order-list) warns of asynchronous delay. Beyond seven days it retains only orders with fills; canceled/rejected/deactivated queries have tighter windows. Consequently even an authorized query does not establish a complete order-decision ledger.

[Authentication guidance](https://bybit-exchange.github.io/docs/v5/guide) requires signed headers for authenticated endpoints and specifies regional endpoints/access restrictions. Those restrictions cannot be converted into permission to switch regions. No API key was requested, created, read or used. A future owner-authorized read-only account adapter would need a separate scoped decision; Copy Trading's order permissions are not appropriate research authorization.

[Copy-trading API guidance](https://bybit-exchange.github.io/docs/v5/copytrade) concerns Master applications and placing orders with API permissions. It is **not** evidence of a public historical Master-trade endpoint. No such documented disclosure endpoint was located in the reviewed official V5 pages; absence from this audit is not proof that no future/partner product exists.

## 3. Public profile disclosure differs from private User Center

[Master performance help](https://www.bybit.com/en/help-center/article/How-to-Understand-Master-Trader-Performance), updated 2026-04-16, describes Classic USDT-perpetual current/past disclosures, including position quantities, entry/open/close information and selected performance fields. It reports transaction display as real-time, several statistics daily, and some charts hourly. This is a documentation claim, not a measured publication-latency SLA. It does not establish a complete lifetime fill ledger, historical revisions, guaranteed fees/funding detail, an export API or unrestricted anonymous access. A 90-day ROI metric is not proof of 90-day trade retention.

[Master/follower differences](https://www.bybit.com/en/help-center/article/Differences-Between-Master-Traders-and-Followers-in-Copy-Trading) documents strategy protection: trades/assets may be visible only to followers/whitelisted users. Signal protection also varies audience. Follower positions can differ and must not substitute for the Master's executions. Public alias/ID, visibility, data fields and revocation must be recorded per actual authorized source; protected rows stay unavailable.

[Master User Center](https://www.bybit.com/en/help-center/article/How-to-Navigate-Copy-Trading-User-Center-Master-Trader), updated 2026-09-07, describes the owner's account/positions/fees and past closed positions. Entry may mean average position price rather than an individual fill. Its 90-day ROI updates at 03:00 UTC while some cumulative metrics update at midnight. This private-view field set must not be projected onto public profiles.

**UNKNOWN:** public profile's login requirement for each field, unprotected profile pagination/maximum lookback, guaranteed delay, deletion/revision behavior, anonymous rate limits, complete market/account coverage and a permitted automated export mechanism. No profile was scraped, protected page opened or undocumented website API inspected.

## 4. Owner-provided exports: possible, explicitly delayed, not third-party public

[Official self-export help](https://www.bybit.com/en/help-center/article/How-to-Self-Export-Account-Data) describes owner-selected account/subaccount exports, CSV order/transaction data, and Copy Trading P&L/account records. Current-day data becomes exportable next day; generation may take 1–3 days; download links last seven days. It states one year per export and 50 exports/month. It also says up to five years/from specified product dates in one section but three years in another: **actual retention is unresolved**, not a promised five-year feed. P&L exports are not automatically fill-level histories. Actual files/filters must be checked.

Only a voluntarily supplied authorized file may enter the future offline import gate. Preserve original bytes/hash, exporter-declared scope, observed time and revision; remove unnecessary UID/legal-name/account data. Consent to private analysis does not imply public redistribution permission. Export instructions alone do not authorize extracting another user's account.

## 5. Registration, region, rate and reuse gates

[Copy Trading terms](https://www.bybit.com/common-static/compliance/legal/BYBIT/57a09e8f2f0c28ac3f416b0358651737.pdf), clauses 3–4 and 7, describe KYC/eligibility, jurisdiction restrictions and limited social-trading information in the service. They do not provide an established third-party bulk-data/redistribution license. Product eligibility and general-site accessibility are separate; this PDF's jurisdiction list differs from the general restricted-countries help. Determine the applicable entity/product/version rather than infer permission from an accessible help page. No registration/KYC was undertaken and no user's residency inferred.

[Current platform terms landing page](https://www.bybit.com/en/legal/terms-of-service/Bybit-Platform-Terms-and-Conditions) returned only its title through this tool; its current operative clauses were not verified. The old `/app/terms-service/` URL redirected to the homepage despite a search cache containing restrictive historic text. That cache is **not used as current legal assurance**. Reproduction, derivative use, retention, alias processing and redistribution rights remain UNKNOWN until applicable terms/permission are established; no unrestricted license was found.

[API rate rules](https://bybit-exchange.github.io/docs/v5/rate-limit) describe a default HTTP IP ceiling of 600 requests/5 seconds, throttling/ban behavior and endpoint/UID limits with response headers. That ceiling is not a polling recommendation, scraping authorization or copy-profile API limit. No private endpoint was tested. Profile-specific machine-access/rate rules remain UNKNOWN.

[Pro FAQ](https://www.bybit.com/en/help-center/article/FAQ-Copy-Trading-Pro) and [Pro rules](https://www.bybit.com/en/help-center/article/Key-Rules-for-Copy-Trading-Pro) distinguish its investment-strategy structure and daily NAV timing from Classic copying. Do not treat Pro NAV, TradFi positions, Classic trade history and own account orders as equivalent observed executions.

## Concrete implementation decisions

- **Allowed now:** documentation registry, explicit UNKNOWN/no-source fixtures, synthetic pipeline tests, existing anonymous market context without identity joins. This audit adds no acquisition permission beyond the current task.
- **Conditional later:** owner-provided CSV import after explicit scope/consent and field verification; public disclosure adapter only after documented machine-access route, actual permitted access and applicable reuse/privacy terms are evidenced. No source gate is satisfied merely by public visibility.
- **Blocked:** named-trader inference from anonymous prints; arbitrary UID queries; private/protected profile access; keys/login/copy subscription to unlock data; undocumented web endpoints; scraping or bypass; assumed complete history/SL/TP/fees/leverage; automatic hypothesis-to-backtest execution; any real trading.
- Keep event_time, published_time (nullable), observed_at, source revision/hash, alias visibility, access scope and acquisition terminal evidence separate. If publication time is unknown, never replay the disclosure at execution time as though historically available. Position/P&L summaries are not inferred entry/exit fills. No performance or profitability assurance follows from the source metrics.

All research used official Bybit documentation/help/legal pages and tester issue reads. No source/product/remote edits, login, keys, trading, private API calls, scraping or cross-repository dependency was created. This is a technical source-gate audit, not legal clearance.
