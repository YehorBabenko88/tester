# Standalone trader intelligence — source availability audit (2026-11-07)

This is a documentation-based audit, NOT a live authenticated provider endpoint smoke. No private API or unauthorized scraping is enabled.

| Source | What is confirmed | Can we identify a trader from anonymous market prints? | Registration/authorization | Adapter state |
|---|---|---|---|---|
| Bybit V5 recent public trades | execId, symbol, price, size, taker side, timestamp; public market executions | **No** | Public read endpoint documented; no trader ID in market trade schema | Existing A market ingestion; trader association BLOCKED |
| Bybit Copy Trading UI | Master-trader performance and some current/past trade fields visible in product help; follower's own order history available | Only where source explicitly discloses the trader/position | Access, permitted automated retrieval, history extent and API not verified | NOT_IMPLEMENTED, SOURCE_ADMISSION_REQUIRED |
| Bybit V5 Copy Trading documentation | Copy-trading order workflow requires account key and trade permissions | Not a public lead-trader history feed | Authenticated trading, **must not enable** | PROHIBITED for tester |
| Binance Spot Copy Trading UI | Lead portfolio metrics; own copier portfolio trade history documented | Not automatically public per-trader execution feed | Logged-in account for own history; third-party programmatic disclosure not established | NOT_IMPLEMENTED, SOURCE_ADMISSION_REQUIRED |
| Authorized user CSV/JSON export | Exact rows voluntarily supplied by owner, subject to source provenance | Yes if original export explicitly includes identity | User consent/source rights required | Canonical local importer implemented; consent attestation required |

Sources:
- https://bybit-exchange.github.io/docs/v5/market/recent-trade
- https://bybit-exchange.github.io/docs/v5/copytrade
- https://www.bybit.com/en/help-center/article/How-to-Navigate-Copy-Trading-User-Center-Follower
- https://www.bybitglobal.com/en/help-center/article/How-to-Understand-Master-Trader-Performance
- https://www.binance.com/en/support/faq/detail/abc27f5949cf46d9801d31b5e33d0b38
- https://www.binance.com/en/support/faq/detail/b9e5e3b2141149be826685d2c88536fa

## Provenance and confidence

Market prints = anonymous; disclosed trader execution = only explicit linked records; inferred event = model hypothesis. Source publication timestamp and event timestamp are separate. Do not compute verified net PnL from executions lacking position/cost completeness. The first implemented core emits overlays with DISCLOSED or INFERRED labels, exact Decimal strings, deterministic SHA-256 digest and an as-of gate preventing later disclosures from leaking into historical analysis.

## Next engineering steps

A: official-source discovery/admission matrix, opt-in authorized CSV/JSON import (no unapproved scraping). B: dedicated storage migrations with immutable revision/evidence and deletion/retention. C/H: position matching with partial close/fees/funding and explicit ambiguity. E: bounded read-only API and durable analysis jobs; D: truthful marker overlays; F: replay inferred rules in offline simulation; G: typed Codex research actions. MASTER: exact-head contract and integration tests.

## Fresh official-source check — 2026-11-07

Official documentation was read over the network during this execution. No account
was created, no key requested, and no trader API/UI history was scraped.

| Source / official reference | Findings | Admission decision |
|---|---|---|
| [Bybit Master performance](https://www.bybit.com/en/help-center/article/How-to-Understand-Master-Trader-Performance) | Trading History describes USDT perpetual transactions and daily aggregate refresh. N-day metrics do not prove complete lifetime fills, costs, transfers or initial inventory. No guaranteed bulk history window or download API established. | UI may disclose trader-linked records; automated acquisition UNCONFIRMED, disabled. |
| [Bybit FAQ](https://www.bybit.com/en/help-center/article/FAQ-Copy-Trading) | Individual KYC Lv1 or Business KYC required for Copy Trading Classic; API link leads to V5 trading guide. Registration and product use are distinct from permission to download third-party histories. | No registration performed. |
| [Bybit V5 copy trading](https://bybit-exchange.github.io/docs/v5/copytrade) | Master application, mandatory Contract Orders & Positions permissions, order creation. This is not evidence of a public read-only per-master history feed. | Trading adapter prohibited in this research workstream. |
| [Binance signed API](https://developers.binance.com/en/docs/catalog/advanced-trading-copy-trading/api/rest-api/future-copy-trading) | Futures lead status and symbol whitelist require key/signature; userStatus has weight 1. Neither is a public per-trader execution history endpoint. | No account/trading key adapter. Trader history window/rate limits UNKNOWN. |
| [Binance copy guide](https://www.binance.com/en/support/faq/detail/abc27f5949cf46d9801d31b5e33d0b38) and [Spot FAQ](https://www.binance.com/en-AU/support/faq/detail/1826c3b426a149949851554bdde227d3) | Own account login/history; FAQ specifies verification, address and eligible regions. Product/region rules must be rechecked for each jurisdiction. No universal full third-party portfolio history established. | Public-history automation/redistribution rights UNCONFIRMED. |
| [Bybit terms route](https://www.bybit.com/en/legal/service-specific-terms/Copy-Trading-Terms-Conditions) | Browser extraction returned heading only. No machine-readable permission grant was obtained. | Terms review incomplete; no assumed scraping/download/redistribution grant. |

Public visibility is insufficient evidence of automation permission. This audit does
not assert that automation is legally forbidden: it records that permission was not
confirmed. Provider-specific trader-history adapters therefore fail closed. The only
enabled adapter is user-authorized canonical CSV/JSON; no promise of native exchange
export compatibility. Full history, publication delay, privacy/redistribution, update
cadence and retention restrictions must be established for each admitted dataset.
Source rights reference, immutable source revision, raw SHA256 and synthetic flag are
mandatory. Revocation disables all dependent research artifacts. No real public
trader dataset was acquired or represented by the synthetic acceptance fixtures.

Status: **H_LOCAL_RESEARCH_IMPLEMENTED**; public history acquisition and cross-agent
HTTP/Codex/official chart integration remain unverified. See README and TEST_REPORT.
