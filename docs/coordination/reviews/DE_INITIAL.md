# Independent D/E review

Repository: YehorBabenko88/tester only. D: 421f22ba3a2c377c16f08b3f0f5b79417078e98f, issue #4. E: 36c939fcfb36ec8b8e7e529f1f1cdda8083a3279, issue #5. Baseline references below are frozen d996b8669e3e85f559fe4adba768bad6d994c9c5.

GitHub compare and complete recursive trees show each workstream is one commit ahead and 14 behind baseline, sharing merge base d36aa458c3efad876a15b0dd345079f0ff20ae0f. D adds only frontend/index.html; E adds only tests/test_quality.py. These are separate snapshots, not complete alternative implementations of the Streamlit baseline. No product or remote changes made.

## Blocking findings

1. **[P1] D cannot load any instruments against its own tree or the frozen baseline.** frontend/index.html:12 fetches `/api/instruments?category=linear`, but D's complete tree has no server/API implementation or launch script. The baseline serves Streamlit app.py, which also defines no such route and never serves frontend/index.html. Refresh necessarily fails unless an undocumented external server is supplied. Define and integrate the endpoint plus static serving before claiming the screener works.

2. **[P1] D is a placeholder rather than issue #4's functional UI.** frontend/index.html:6-7 offers only symbol/status columns and explicitly says loading/charts/metric sorting await API integration. Lines 9-13 have no selected-symbol state, download action, timeframe, market selector, progress, provenance, quality badge or chart implementation. The checkboxes are reconstructed on every search/render (line 11), losing checks even when the symbol remains visible. Acceptance needs integrated behavior and automated UI tests, which the complete D tree lacks.

3. **[P1] E's entire test suite imports a module absent from its commit.** tests/test_quality.py:2 imports `src.quality.candles.inspect`; E's complete tree contains no src directory. Explicit discovery fails with ModuleNotFoundError rather than running the three tests. This is a real branch integration failure, not evidence the candle classifier itself fails. E's tree also lacks the API endpoints, PostgreSQL setup, packaging, CI and scripts required by issue #5; README.md:3 defers operational instructions to upcoming files. The good Windows instructions and CI in baseline are not present in this workstream snapshot.

4. **[P2] E tests are skipped by the frozen baseline CI command.** tests/test_quality.py:1-14 is nested in tests/ without tests/__init__.py (verified complete tree); baseline .github/workflows/tests.yml:15 invokes root `python -m unittest discover -p "test_*.py" -v`. Non-package test subdirectories are not recursed by this command. On integration with baseline, existing root tests could make CI green while all three new tests remain unexecuted. Add explicit `-s tests` discovery or package discovery and resolve finding 3.

## Frozen baseline issues relevant to D/E acceptance

5. **[P2] The “1 hour” screener computes 65 minutes.** app.py:34 requests start=end-65*60000; line 35 filters only the upper bound; lines 37-39 include all 65 returned candles. An extreme in the five extra minutes can change rank materially. An isolated execution of the actual AST-extracted function using 65 synthetic closed candles produced 101% while the last 60 minutes yielded 2%. Trim to end-60*60000 and verify the intended coverage.

6. **[P2] Historical chart has no date-range filtering or history navigation.** app.py:228 and :247 call db.series with no begin/end, despite the date controls above; :240 and :259 similarly omit range for delta/footprint. db.py:93-103 series defaults to the newest 4000 rows and db.py:113-118 footprint to 700 price levels. Downloading an old requested period after newer history exists can display only newer data, and chart pan cannot fetch omitted records. Baseline README.md:26 claims chart support without describing this display cutoff. Scope chart queries to the selected dates and support paging/aggregation for larger ranges.

## Security, setup and limits

D renders exchange symbol/status through textContent (frontend/index.html:11), avoiding obvious HTML injection in those cells; fetch is read-only. No trading credentials/orders or browser-side database password were found in the reviewed added files. This is a static observation, not a security certification. E does not implement a server, so endpoint binding/authentication/security cannot be evaluated; their absence is covered by finding 3. Baseline README Windows instructions correctly require an independently running PostgreSQL server and do not claim to install/reset it. Baseline CI runs only Ubuntu/Python 3.12 and has no PostgreSQL service or browser test, so it does not validate Windows setup, live persistence, chart interactions or startup. Do not equate its unit-test pass with those acceptance criteria.

## Verification performed

- Read-only GitHub API issue #4/#5, immutable file fetches, complete recursive trees, and compare metadata for both specified SHAs.
- Executed on Windows Python 3.14.0, in isolated work/review-e-isolated: root unittest discovery reports 0 tests (exit 1 on this interpreter); explicit `-s tests` discovery reports one failed import, ModuleNotFoundError: src. The isolated test is the exact fetched E file; no unrelated dependencies are needed to establish the missing module.
- Executed AST-extracted frozen one_hour_range with a fake API (no network, no Streamlit/DB), confirming finding 5.
- All UI, security, CI and Windows documentation conclusions otherwise static. No live browser, PostgreSQL server, dependency installation or Bybit call was executed. Python 3.12 CI behavior was not executed locally.


