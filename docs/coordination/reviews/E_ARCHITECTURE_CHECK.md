# E architecture integration gate — exact 0a17033

Immutable E PR9 candidate **0a17033f6c88b336e988e4007231a2ee9cefe761**, GitHub complete tree/files and compare vs6d8b1b97433fd850a8900ad8a3750b682319dc3a (one commit ahead). Isolated work/e-architecture-check. Also reviewed mutable ROOT work/master-supervisor/src/integration/standalone_policy.py and six tests by request. No production/source/remote changes; isolated reviewer probe only.

## Previous scope P1 resolved

Exact published FastAPI `/api/trades` endpoint function now rejects the earlier request category=spot/symbol=ETHUSDT/range[0,60000) with provider BTCUSDT/linear/timestamp1700000000123. It raises ScopeError. An adverse provider that rewrites its supplied query.category/symbol/start/end also fails; the original authoritative request remains unchanged. main.py copies the query for provider use and binds against original query, then signs cursor.

Independent `probe_architecture_review.py` calls actual endpoint then actual canonical boundary, not a rewritten implementation. Correct request retains exact source rows, decimal strings, provenance/availability/coverage and wraps continuation in e1 HMAC token. Cross-symbol cursor reuse fails. Existing source tests additionally cover venue/candle-kind/resource/row limit/coverage boundaries, mixed scopes, tampering and restarted cursor keys. The prior metadata/NaN/complete-promotion fixes remain in place.

## Remaining concrete issue

**[P2] Bar duration is checked, UTC bucket alignment is not (except M).** src/api/scope.py:_bar_interval accepts numeric/D/W solely by end-start width. A canonical official_trade weekly candle with timestamp_ms=start_ms=1, end_ms=604800001, interval=W passes bound_response for an identically requested range. Epoch+1ms is Thursday+1ms, not Monday UTC; the same flaw permits daily/minute off-grid offsets. Current test_footprint_without_interval_field_binds_utc_bar_window even treats W[0,604800000) as valid (Thursday-anchored). Wrong-week boundaries can misalign C/D historical context. Enforce minute-grid, UTC midnight D and Monday-midnight W starts/ends for exchange bar kinds, retaining real month boundaries; add off-grid regressions. Do not reject arbitrary event timestamps merely because they are not bar-aligned.

**Integration interval mapping needs an explicit adapter.** scope.py requires every observation row.interval to equal query.interval. Canonical funding fixture with interval='event' is rejected on default Query interval='60'; current D sends the viewport interval to `/api/funding` (and `/api/open-interest`). This is reproducible ScopeError, not fabricated unavailable data. Resource-specific sampling/event intervals must be selected in tester's own E/D adapter (e.g. funding event versus candle viewport), with a documented mapping for OI1h vs display60. No external Gateway is needed. This is an integration readiness gap; requesting interval=event directly can satisfy E, so it is not a claim the funding endpoint universally rejects valid data.

Coverage-only rows still lack category/symbol in pinned v1 shared schema, so only their time ranges are bound. Source capability rows likewise lack a shared row contract. These are explicit schema/interface limitations; do not claim market identity binding for fields those records do not carry.

## ROOT standalone ownership policy

Read source and executed `../venv/Scripts/python.exe -m unittest test_standalone_policy -v` in work/master-supervisor: **6 PASS**. Allowlisted tester-owned src component modules, no arbitrary paths/URLs/code syntax, missing/ambiguous module rejection, resolved-source escape checks and final loaded-origin comparison are appropriate ownership guards. Tests reject named foreign app factories and wrong loaded origins. No concrete flaw found within this narrow purpose.

The module explicitly does no imports and is not a Python sandbox. Final E/G loader must keep tester-owned import paths authoritative, apply check before import and assert_loaded_origin before callable invocation. This does not certify arbitrary imported Python code, dependency behavior or process isolation. No foreign repositories were accessed to test denials.

## Tests actually executed

- Exact E selected direct request-scope plus canonical suites: **28 PASS** (0.82s), with one Starlette/httpx deprecation warning. Selection explicitly lists nonHTTP scope tests and all test_canonical.py.
- Additional exact actual-endpoint probe: wrong scope rejected, mutated provider query rejected/original retained, positive lossless metadata, signed cursor/cross-symbol rejection, off-grid W acceptance, funding event/default60 mismatch reproduced.
- ROOT standalone policy six tests PASS.
- Initial mixed test selection inadvertently included a TestClient case; pytest-timeout fired while anyio.from_thread.start_blocking_portal waited to start, before a completed HTTP assertion. This is the same child runtime/socket limitation; no HTTP pass or product scope failure is claimed from it. Do not rerun indefinitely. Root can execute full HTTP fixtures under its full-access process:

```
../venv/Scripts/python.exe -m pytest tests/platform/test_request_scope.py tests/platform/test_api.py tests/platform/test_http_contract.py -q --timeout=10 --timeout-method=thread --basetemp=../pytest-e-architecture-http-root
```

No real PG/service/browser/exchange test was part of this review. This gate is ready for root's pinned internal A-G candidate with the UTC-alignment/resource-interval findings explicitly tracked; it does not certify complete end-to-end acceptance.
