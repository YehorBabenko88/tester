# Standalone trader intelligence — source availability audit (2026-11-07)

This is a documentation-based audit, NOT a live authenticated provider endpoint smoke. No private API or unauthorized scraping is enabled.

| Source | What is confirmed | Can we identify a trader from anonymous market prints? | Registration/authorization | Adapter state |
|---|---|---|---|---|
| Bybit V5 recent public trades | execId, symbol, price, size, taker side, timestamp; public market executions | **No** | Public read endpoint documented; no trader ID in market trade schema | Existing A market ingestion; trader association BLOCKED |
| Bybit Copy Trading UI | Master-trader performance and some current/past trade fields visible in product help; follower's own order history available | Only where source explicitly discloses the trader/position | Access, permitted automated retrieval, history extent and API not verified | NOT_IMPLEMENTED, SOURCE_ADMISSION_REQUIRED |
| Bybit V5 Copy Trading documentation | Copy-trading order workflow requires account key and trade permissions | Not a public lead-trader history feed | Authenticated trading, **must not enable** | PROHIBITED for tester |
| Binance Spot Copy Trading UI | Lead portfolio metrics; own copier portfolio trade history documented | Not automatically public per-trader execution feed | Logged-in account for own history; third-party programmatic disclosure not established | NOT_IMPLEMENTED, SOURCE_ADMISSION_REQUIRED |
| Authorized user CSV/JSON export | Exact rows voluntarily supplied by owner, subject to source provenance | Yes if original export explicitly includes identity | User consent/source rights required | Normalizer foundation implemented, file importer pending |

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

Status: **FOUNDATION_ONLY**; no live trader-source acquisition, persistent trader tables, API, Codex connection or UI overlay integration verified.
