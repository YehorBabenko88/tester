# Nov 9 independent D/L frontend review

Pinned sources: PR46 `94868a60a600831c5773153de058fcf909c6ba8d`; PR39 `0ec0f9aebd67a811bd6254b8afbe38b5e0a742e9`. Reviewed immutable Git objects, not the mutable candidate worktree. Root may switch branches without changing these results. Host is PC4, **not ADMINIT**.

No app server, browser, installed application, native PostgreSQL/service, Office connection or installation was started/probed. No shared source or branches were changed. Only this report is written. GitHub reads were repository/PR metadata only, not host connections.

## Concrete finding

**[P2] “Unavailable last” uses different definitions in the server and table.** PR46 frontend/catalog-ui.js:14-24 hides values unless their state is OBSERVED/STALE with a safe source time. However src/api/market_catalog.py:396-405 places every non-null numeric value in the sortable present partition, regardless of state. Query freshness handling at :365-374 explicitly retains UNKNOWN/source_time_* states with their original value. Therefore a mixed page sorted descending can put an UNKNOWN value100 before an OBSERVED value1; the actual table renders `UNAVAILABLE, 1`, contradicting app.js:346 and README's global “unavailable last” claim.

Repro fixture: two same-category rows, metric A `{value:'100',state:'UNKNOWN',source:'fixture',source_time_ms:null,reason:'source_time_missing'}`, metric B `{value:'1',state:'OBSERVED',source:'fixture',source_time_ms:<fresh time>}`. The server's actual partition/sort source puts A first for descending; exact pinned catalogMetric execution returns `[UNAVAILABLE,1]`. This is an offline source/adapter counterexample, **not a claim that a live catalog currently contains those rows**. Add a backend global-pagination regression with mixed displayable/UNKNOWN metrics and align the server's absent partition to the UI contract. Do not repair it by locally re-sorting50 rows, which would lose full-snapshot ordering.

## What the reviewed implementation gets right

- Table requests server-global search/status/contract/watchlist filtering and sorting before50-row pagination; no frontend numeric re-sort. Repeated watchlist query parameters are serialized correctly. Headers cycle asc/desc/none. Linear and inverse selection/job keys retain category identity; the table is one shared layout with a category filter, not an assertion that both categories are fetched in one request.
- Selected identities, saved watchlist, sort and filters persist. PR46 saves search synchronously before the180ms debounce, so immediate reload retains it. Controlled execution of the actual input handler confirmed this; it did not require a browser.
- Revision is pinned while paging;409 resets to page1 and retries once, preserving filters/selection. Repeated conflicts disable stale pagination. Source code guards stale aborted requests before publishing rows. Failed reads retain rows with a visible STALE warning rather than fabricating successful freshness.
- Browser cache reads run every30s while visible. It does **not** initiate a private5-minute acquisition loop; shared backend scheduling/next_refresh_at owns that refresh and backoff. No physical observation of that scheduler is claimed here.
- Inline downloads retain independent IDs/request identities, persist tracking across reload, match rows by category:symbol and show source/timeframe/range. Work progress and dataset coverage stay separate; missing denominator/completion checks remain indeterminate. Existing progress/telemetry tests pass. This does not prove actual on-host ingestion or committed-byte counts.
- Chart navigation remains bounded by ranges/pages; W/M and OI native cadence logic remain present. Pure geometry/time/canonical tests pass. The research renderer keeps synthetic/SIMULATED labels, uncertainty windows, source/run/snapshot binding and exact decimal strings; digest verification precedes acknowledgment. Failed acquisition/render/ack clears old artifact state. Server/host authentication, native gestures, actual pixels and persisted acknowledgment were not exercised.
- PR46 and PR39 have **identical blobs** for research-app.js, research-chart.js, research.html and overlays.js. Catalog work did not replace the separate research page/assets. Their presence does not automatically connect/authorize research routes; route mounting and typed persisted artifacts must still be validated on the assembled candidate under separate consent.

## Overlapping PRs — avoid duplicate or stale snapshot consumption

| PR | Read head | Relationship to the pinned frontend |
|---|---|---|
|49|dffbae52fdc1fc93d3409e1f8271e6408bf44388|Earlier unified-table contribution; largely overlaps PR46. Two-head delta adds later pagination/search/acceptance changes. Preserve latest PR46 implementation rather than replacing it with this older tree.|
|51|9290c2fdf69c818bae90a437df77b7b0bab3ba24|Runtime identity/security plus revision-pagination follow-up. Frontend differs from PR46 only by the later immediate-search persistence fix/test. Security contribution needs separate source-level composition review; pure UI tests do not certify owned-process identity.|
|52|4018387264a597e3323ee32cb04edd1125528c69|Frontend tree matches PR46; synchronous search persistence and regression already present. Avoid duplicating or undoing it during conflict resolution.|
|53|0891e253821db07b1912113fbdce90ddf1c315e7|Actual three-dot contribution is docs/raw benchmark JSON/benchmark script only. Merge base with PR46 is21492bb, so a normal three-way merge preserves PR46's search fix. Its **whole HEAD snapshot** lacks that later fix; do not use PR53 HEAD as a replacement application tree.|

The initial two-head PR53 comparison looked like a search-fix revert; checking the merge base corrected that interpretation before this final report. **No runtime-revert defect is attributed to PR53's actual patch.** Controlled exact-handler probes confirm PR46 stores search immediately while the older PR53 snapshot waits for debounce. This is a snapshot-selection risk, not a new PR53 runtime edit. Benchmark source market_catalog.py has identical Git blob4d65ec9a238de705fc43ba804236b268906596ef at21492bb and PR46; measurements remain synthetic in-memory evidence, not API/database/UI/concurrency or current-host acceptance.

## Verification actually performed

Ran all pure Node test files from each exact Git snapshot **in memory**, using `C:/Program Files/nodejs/node.exe`, experimental VM modules, native node:test/assert, and existing tester scratch Ajv dependencies. Relative source/fixture reads were resolved with `git show <exactSHA>:<path>`; no checkout/export or source edits were needed.

- PR46:6 pure test files, **36 PASS**.
- PR39:5 pure test files, **33 PASS**.
- Combined final run: **69 PASS,0 failures**. This includes canonical state/reason/coverage, exact decimal sorting helpers, progress, OI gaps, timeframe geometry, source permission/anonymous-trade overlay denial, synthetic research timing/identity, and Python-compatible canonical digest fixtures.
- Additional pure Node probes: exact search handler persistence before debounce and UNKNOWN metric display conversion. No backend request was sent.
- Initial reviewer harness problems (Windows virtual-file URL and dynamic-import callback) were corrected; they were harness failures, not product findings. Final69-test result supersedes those attempts.

No Playwright/spec/e2e/backend benchmark was executed. Existing browser tests and authors' pass counts were read as source/PR evidence only. There is **no new physical UI, installed-host, PC4-native, ADMINIT, PostgreSQL or Office acceptance claim** from this review. I found no additional reproducible P1 in the reviewed D/L source; the sorting/display contract mismatch above remains the actionable P2.
