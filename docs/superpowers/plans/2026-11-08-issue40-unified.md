# Issue 40 unified market workspace implementation plan

> For agentic workers: use subagent-driven development with isolated A, E and D/L worktrees. The user's Issue40 specification and explicit execution request define the product requirements.

**Goal:** One compact globally sorted market/download table using the complete observed Bybit futures catalog and one shared five-minute backend refresh owner.

**Architecture:** Extend the reviewed PR41 foundation on accepted PR39 source A43. Public source acquisition, durable API/scheduling, and frontend are independently developed; the coordinator integrates their published commits and tests an immutable GitHub candidate in a newly owned portable lab. The installed native release remains 719b02e.

**Tech stack:** Existing FastAPI/psycopg/PostgreSQL, verified HTTPS Bybit V5 public endpoints, vanilla JS and Playwright. No real orders, main merge, UAC, forced worker recovery, PG5432 or office-PC changes.

**Spec:** https://github.com/YehorBabenko88/tester/issues/40 and the current user directive.

## Tasks and ownership

- [ ] A: `src/api/bybit_catalog.py`, acquisition/normalization seam in `market_catalog.py`, source tests and independent public Bybit evidence. Exhaust cursor pages per category/status with budgets and preserve actual returned status, raw contract type, settlement and perpetual/dated classification. Repeated cursor, conflicting duplicates, partial failures and 429 must never publish false completeness.
- [ ] E: `catalog_store.py`, explicit checksummed SQL migration, `persistent_market_catalog.py`, router/config/main composition and tests. Default off; separate explicit catalog DSN; no startup/request DDL. PostgreSQL session ownership ensures one refresh owner per category across processes. Publish snapshots atomically, persist attempts/manual requests, retain stale values, coalesce refreshes, back off and stop cooperatively.
- [ ] D/L: frontend index/app/styles plus tests. Combine the screener and downloads, preserve 13 intervals and existing charts, render bounded rows with sticky sortable headers, inline fenced job state and collapsible diagnostics. Every metric sort/filter/watchlist applies on the server before pagination. Preserve category/symbol selection and preferences across refresh/reload.
- [ ] Coordinator: backward-compatible download adapter hooks and an opt-in full-catalog handler/reader, an isolated published-only market lab harness, integration reviews, actual source/API/PostgreSQL/browser tests and screenshot.

## Shared interfaces

`GET /api/market-catalog/{linear|inverse}` retains the foundation envelope `data={rows,total,offset,limit,revision,catalog,attempt}`. Query supports global search, metric sort, asc/desc/none, contract_kind/status/settle filters and repeated watchlist keys. NULL values stay last, ties are deterministic and revision conflicts restart pagination. Cached reads never fetch Bybit.

`POST /api/market-catalog/{category}/refresh` only queues/coalesces a shared backend signal under the existing same-origin policy. Automatic refresh is 300 seconds with explicit scheduling metadata and bounded failure backoff. Browser tabs may read cached state but never own source polling.

Rows retain metric values as exact decimal strings with provenance and source/observation timestamps. Missing market cap, volatility or unqualified one-hour/OI history remains UNAVAILABLE. The source response time does not establish a last-trade timestamp or historical completeness.

Downloads retain `/api/downloads` and immutable job IDs/versions. Multiple jobs per instrument remain separately visible. Download COMPLETE never promotes Coverage UNKNOWN/PARTIAL to VERIFIED. Unknown denominator means indeterminate progress.

## Review focus and acceptance

- [ ] Catalog: >1000 synthetic rows plus independent real cursor enumeration; observed status differs from requested filter; linear/inverse and perpetual/dated separation; duplicate/repeated cursor/budget/429 failure.
- [ ] Query: global Decimal sorting/search/filter before page slicing; nulls and ties; stale ETags; changed revisions; global favorites and category-safe keys.
- [ ] Scheduler/store: two processes cannot source-refresh the same category concurrently; queued manual signals survive rate budget; failure preserves prior snapshot; stop fences publication; explicit migrations only and no native DSN fallback.
- [ ] UI/jobs: all headers, preserved selection/sort/search, bulk and row download, overlapping jobs, restart-restored job IDs, UNKNOWN coverage, 13 intervals, narrow viewport, keyboard/mouse and bounded DOM on large catalogs.
- [ ] Integration: full CI on exact published SHA; newly owned PG55471/API18767 lab, real verified Bybit data and history jobs, actual backend browser E2E without route mocks, measured large-universe query/DOM performance and final screenshot. Preserve native identity/PIDs and retain test data after normal cleanup.

No release GO follows from partial component tests. Publish the final source SHA, source manifest, CI results, actual-data evidence and screenshot; keep remaining unavailable metrics and gates explicit.
