# Paired E/D UTC grid and observation-cadence review

Exact local Git objects exported read-only from tester's own work/master-supervisor repository:

- E b15b1ce12633a134b19cb71a57d7e841a7120579 → work/e-grid-check.
- D 6cf9a2af19e05f57f6f3d18fbda8038d9bc0f89c → work/d-grid-check.

No GitHub calls, remote changes, integration or product edits. Scratch probe and test dependencies only; no other repositories/services accessed. Older e-architecture-check/d-heartbeat1 snapshots were not treated as these heads.

## Verified fixes

- E _bar_interval now rejects off-grid minute/hour/D/W starts, bool/float wire values, and incorrect durations. W anchors to1970-01-05 Monday UTC; M uses integer epoch+timedelta and real month transitions. Previously reproduced W Thursday+1ms acceptance is fixed.
- E resource_query separates viewport interval from source cadence: funding defaults to event and rejects other explicit source cadences; OI defaults5min independently of viewport spelling, accepts explicit5min/15min/30min/1h/4h/1d, rejects unsupported combinations.
- Actual FastAPI endpoint functions executed with controlled canonical providers: funding event and OI5min rows survive60→D viewport changes unchanged; provider receives interval/source_interval=native cadence and viewport_interval=display cadence. Signed raw provider cursor is decoded/reused for display-only changes. Explicit OI cadence change5min→1h rejects old cursor. Canonical wrong returned OI cadence1h for requested5min is rejected. ViewportM also retains default funding event/OI5min.
- D current app.js sends viewport interval plus source_interval=event for funding, explicit OI selector value (5min default) for OI. Client paging identity includes source cadence; selector change invalidates paging. Display-only changes conservatively reset D paging, compatible with E allowing reuse. No browser resampling or analytics fallback found.
- D chart line continuity now uses OI source cadence independently of display geometry; event series does not infer a regular sampling gap. Existing continuity test passes. Existing canonical availability/reason/provenance and conservative completeness regressions pass.

## Actual tests

- E direct UTC-grid + canonical + direct scope/cursor selected suite: **36 passed**, one Starlette/httpx deprecation warning. Command in work/e-grid-check:

```
../venv/Scripts/python.exe -m pytest tests/platform/test_grid_source_intervals.py::test_source_bar_rejects_off_grid_and_non_integer_times tests/platform/test_grid_source_intervals.py::test_source_bar_accepts_exact_utc_calendar_grid tests/platform/test_request_scope.py::test_provider_boundary_rejects_wrong_request_identity tests/platform/test_request_scope.py::test_cursor_restart_and_unicode_budget_fail_closed tests/platform/test_canonical.py -q --timeout=8 --timeout-method=thread --basetemp=../pytest-e-grid
```

- D `node --test tests/*.test.js`: **19 passed**. Scratch initially lacked Ajv; copied only ordinary test dependency packages from existing tester scratch node_modules (junction denied). Final native suite actually ran including canonical Ajv tests, no dependency skip.
- E `../venv/Scripts/python.exe probe_pair_review.py`: actual route endpoint functions plus canonical providers; all assertions pass for event/5min defaults, separate viewport, unchanged data, cross-cadence cursor rejection and wrong-source response rejection.

## Assessment and limits

No new reproducible defect found within the reviewed UTC-grid/source_interval/scoped-cursor changes. Prior UTC alignment defect and funding/OI viewport mismatch are resolved for these paired interfaces. This is a limited positive assessment, not full integration acceptance.

No real HTTP/TestClient, Playwright browser, PostgreSQL, exchange/provider-live, SCM or performance run in this cycle. D network request construction was statically checked; E actual sync endpoints and direct fixtures were executed without the previously blocked child async transport. Root separately owns HTTP/PG integration. Source adapters must supply correctly scoped canonical native observations and must not mark empty/exhausted data COMPLETE; these tests do not authenticate source evidence or prove real data retention.
