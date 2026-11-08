# D heartbeat 1 — canonical P1 fixed; coverage summary issue remains

Independently fetched GitHub Draft PR12 metadata, complete recursive tree, exact frontend files and compare metadata. Current HEAD confirmed **2615f217b49779d848be425b0682ea24864a208e**, one commit ahead of previously reviewed 1b35ec6565ef0f9471ecd7197a64bf598080f85b. Isolated source: work/d-heartbeat1/frontend. No product, remote or schema edits. Additional probe files only in isolated scratch. E unchanged head was not re-audited.

## Previous findings resolved at exercised adapter boundary

Old P1 canonical UNAVAILABLE-to-PARTIAL promotion is **resolved** at this SHA. Exact exported adaptSeries now combines envelope and row availability by weakest state; controlled AVAILABLE envelope + UNAVAILABLE row remains UNAVAILABLE and renderable=false, rows empty. UNVERIFIED likewise suppressed. PARTIAL renders while retaining source_conflict_fixture reason. Structured provenance source_ref renders as bybit_archive: fixture://source, not [object Object], and full provenance/coverage survives for evidence display. loadCharts now checks series.renderable before drawing. Old nested metric reason loss is **resolved**: missing_side_fixture survives and state remains UNAVAILABLE.

Executed `node probe-heartbeat.mjs`: AVAILABLE/PARTIAL/UNAVAILABLE/UNVERIFIED, envelope-vs-row conflict, provenance/coverage preservation, metric reason, paginated downgrade all **PASS**. Exact source and fixture records retained. No assertion of real-source evidence authentication.

## Remaining concrete issue

**[P2] Panel completeness can still say COMPLETE while inherited record coverage is PARTIAL.** frontend/series.js:96-100 chooses payload.coverage.status before considering record coverage collected by quality; app.js:712 renders that completeness label. Repro with canonical `envelope([candle('PARTIAL')], 'AVAILABLE')` returns availability PARTIAL, completeness COMPLETE, and evidence array containing both COMPLETE envelope coverage and PARTIAL row coverage. The original availability upgrade is fixed, but the summary still presents the stronger coverage assertion rather than conflict/partial status. Keep both proofs inspectable and derive a conservative or explicitly conflicting summary; do not select envelope COMPLETE silently when child coverage disagrees. Exact executed one-line probe output was `{availability:'PARTIAL',completeness:'COMPLETE',coverage:[{status:'COMPLETE',...},{status:'PARTIAL',...}]}`. Neither source is authenticated by UI, so conflict must remain explicit.

Cursor compatibility note: app.js:701-705 permits opaque tokens up to4096; last reviewed E Query accepts at most512. A 513..4096 token will pass D's check then get rejected by unchanged E. Coordinate one versioned maximum. Per-panel cursor history itself is bounded to100 and rejects repeated/cyclic tokens; a new range/symbol/timeframe resets cursor history (app.js:608-628, :668-672). This is static inspection, not browser execution.

## Timeframes, history and strategy status

W/M are now selectable (index.html:141-142). time.js uses Monday UTC weeks and real calendar months including leap years/year changes; shiftRange clamps month-end dates without collapsing a positive interval. charts.js uses bucketWidth for per-bar width and gap boundaries instead of fixed30-day months. Nine existing model/time tests passed. App now offers Previous/Next server data pages, retains one1000-row page per panel, passes opaque token unchanged, and caps history/cycles; beyond1000 navigation is implemented as bounded paging rather than full-history accumulation. Browser cursor interaction tests exist but were not independently executed in this run.

Strategy workspace regions now exist and honestly show DATA_UNAVAILABLE pending validated simulation outputs. This is scaffolding, not an implemented parsed-DSL preview, run simulation, journal, SL/TP overlays or equity/drawdown. No C math was recreated. Keep those Issue17 gates pending rather than calling the workspace complete.

## Actual tests / installation limits

- `node --test tests/model.test.js tests/time.test.js`: **9 PASS**, Node v23.6.0.
- `node probe-heartbeat.mjs`: controlled canonical state/reason/provenance/coverage and pagination assertions **PASS**.
- Pinned fixture schema checked and all four candle + four API envelope fixtures structurally validated with root Python jsonschema: **8 PASS** (uses pinned market-9766c009.schema.json).
- Initial `node --test tests/*.test.js`:9 pass; canonical.test.js fails to load because Ajv absent (dependency failure, not a failed contract assertion). Bounded `npm ci --ignore-scripts --no-audit --no-fund --fetch-retries=0 --fetch-timeout=15000 --cache ../../npm-cache` failed DNS ENOTFOUND registry.npmjs.org in this agent's execution environment. Root was informed. Do not report all15 native Node tests passed from this run unless root subsequently installs/reruns them.
- No browser/Playwright full suite, high-DPI interaction, live E API/provider or database run. Old historical reports explicitly refer to previous heads and are superseded for fixed findings above.

Final dependency update: Ajv became available after root installation. Independently reran `node --test tests/*.test.js` in exact scratch checkout: **15 PASS, 0 failures** (278ms). This supersedes the initial dependency-blocked native-suite result above; all six canonical Ajv tests actually executed and passed.

