# Agent L: opt-in durable research chart

This follow-up adds an actual canvas renderer at `frontend/research.html`, isolated from the normal terminal entry point. It reads K's same-origin authorized research command/result/ACK routes. Backend composition, PostgreSQL durability and actual F execution remain J/K/root owned. It does not change default backend configuration or the installed application.

Frozen projection: `tester.research-chart/2`, flat run/snapshot/revision/market/range/cutoff binding, required exact-string candles (maximum 2000), and at most 500 persisted fill markers. Marker role and buy/sell direction are producer-supplied from actual F fills; IDs are not inferred. Entry triangles, exit crosses and half-open time windows use the same UTC and price transforms as candles. Keyboard, mouse wheel, drag, clamped pan/zoom and high DPI recompute geometry. Exact prices, times and nullable closed-trade links remain in an accessible correspondence table.

The client requires a SIMULATED synthetic artifact with synthetic source references, checks every command binding field and computes the Python-compatible sorted compact ASCII JSON SHA256 before drawing. Premature bar knowledge, future-known fills, duplicate fill IDs, invalid decimals/times and out-of-range events fail closed. A render ACK sends the digest and exact full-range rendered fill IDs only after canvas drawing completes. Failed access, validation or ACK clears the chart. The password capability is cleared from its input after submission and is never saved to local/session storage or a URL.

Labels say SYNTHETIC ONLY and real market coverage NOT PROVEN. External trader events remain DATA_UNAVAILABLE with UNKNOWN permission; the separate fixture-only disclosed/inferred contract does not acquire a live source or gain rights from a chart click.

Verification locally: `npm test` passed 32 unit tests; `npm run test:ui` passed the existing 44 automated headless fixtures. These do not certify the vertical path. The separate `npm run test:research:e2e` suite has no request interception or response mocks and requires a root-provided disposable synthetic backend with real PostgreSQL research records. It verifies denied access, allowed command/result, ACK, canvas pixels, exact fill table correspondence, DPR2, pan/zoom and visible marker/window bounds.

Required environment for that suite:

- `TESTER_RESEARCH_OPT_IN=synthetic-disposable`
- `TESTER_RESEARCH_BASE_URL` = explicit disposable HTTP loopback backend (serving this frontend at `/research.html`)
- `TESTER_RESEARCH_TOKEN` = disposable persisted capability token
- `TESTER_RESEARCH_COMMAND_FILE` = approved command JSON file (without a token)

Actual backend E2E execution is pending root composition; no pass is claimed here. No installed ADMINIT endpoint/service, production PostgreSQL, physical input, acquisition, real orders or main merge were used. Earlier fixture-only document remains historical preparation; this follow-up supersedes its statement that a renderer is absent, but does not imply F/G/H production integration.

Additional renderer-only fixture verification: `npx playwright test tests/research-renderer.spec.js` passed (1 automated headless test). It exercises actual candle/entry/exit/span canvas drawing, zoom/pan bounds and clearing stale geometry; its static fixture routing is intentionally separate from the real-backend E2E class. It is not evidence that API authorization, persistence or F execution is integrated.

Source-only cross-contract validation against exact J adapter `9ee7e1d7779808d094bc1dd13f7ad48d6e1d29fd` executed its actual `run_certified_fixture()` from a task-local Git archive, then passed the generated artifact through this frontend validator and digest implementation. Both produced SHA256 `b2704ca080a0987622b062fa088372894537372a3b8cd15705f518edcd4b6d44` (3 candles; 3 genuine F markers: entry-buy, exit-sell, entry-buy). This used no database/API. A further fail-closed test rejects missing/unsupported coverage and nonsynthetic snapshot namespaces; current unit total is 33 passing. The focused renderer fixture was rerun and passed after that change.
