# F corrective-head review — heartbeat 1

Exact inspected commit: **`998c5bc89d1b35c9ad3047b1e50dcb1295120e31`**, resolved from the fetched `origin/workstream/f-strategy` in `work/master-supervisor`. Extracted the entire pinned Git tree with read-only `git archive --format=zip` into `work/f-heartbeat1`; no product/remote edits. Engine version is now `strategy-bar-1.1.0`.

**Both previously reported P1 findings are resolved for the tested explicit synthetic fixtures.** No new blocker found in this focused corrective review.

- **Protective trigger persistence:** `_exit` creates/reuses and latches a triggered exit order before calculating executable quantity. Zero quantity retains requested/filled quantities, pending order and next eligibility. `_protect` cannot replace a latched market residual after rebound, and gap handling preserves it. An independent fixture re-created the original one-unit entry consuming all bar capacity, low crossing stop 95, then rebound to 100. The exit is now pending after the trigger and fills on following capacity at 100; checkpoint/restore produces the identical result. Current authored tests also cover zero-capacity target/liquidation, gaps and repeated partial exits.
- **Whole-bar liquidity timing:** quality now requires exact bar range, available_at_ms equal to bar close, full AVAILABLE/COMPLETE metadata, provenance and explicit evidence references. Late/premature/wrong-range/partial/unavailable capacities reject even under exploratory price policy. Capacity-dependent fills carry `time_precision='bar'`, `known_at_ms=end_ms`, representative `timestamp_ms=end_ms-1`, time_window and evidence refs; overlays inherit known_at_ms. They no longer claim open precision. README/FORMULAS explicitly retain the open as a possible **price reference**, not proof of open execution, and warn BAR_CAPACITY_TIMING_UNCERTAIN. E/D must honor known_at_ms before showing these uncertain fills. This is still a bounded OHLC participation model, not tick reconstruction.

## Independently observed RED → GREEN

Scratch-only `work/f-heartbeat-probe.py` imports each pinned source tree and the explicit synthetic JSON DSL fixture. It defines two behavioral tests: zero-fill stop survives rebound/restore; future-bar capacity never produces open-precision fill and overlay knowledge precedes no close.

- `work/venv/Scripts/python.exe work/f-heartbeat-probe.py work/f-cycle2-latest old` against former `c72cf505...`: **2 tests, 2 expected assertion failures**, exit 1 (lost pending stop; modeled_open instead of bar).
- `work/venv/Scripts/python.exe work/f-heartbeat-probe.py work/f-heartbeat1 new` against exact corrective head: **2 tests pass**, exit 0. New liquidity metadata was supplied as required; no production adapter or expected result was patched.

## Current branch test execution

- Strategy discovery with root venv, `tempfile.tempdir=os.getcwd()` and tests/strategy on sys.path: **39 methods, 38 pass, 1 ERROR**, exit 1. The same environment restriction remains in `test_artifact_integrity_and_comparison`: Windows sandbox denies `os.link` at artifacts.py:26. Artifact persistence was not independently verified; no full-green branch claim is made. All eight new master-review regression methods pass.
- `../venv/Scripts/python.exe -m unittest discover -v` from extracted root: **9 inherited core tests pass**, exit 0. Root discovery does not include namespace-only strategy tests; the explicit strategy invocation above is necessary.

Only explicit deterministic synthetic DSL fixtures were run. No unresolved natural-language strategy, exchange/order API, network source or PostgreSQL operation was executed. Source authenticity, A–E snapshot adoption, E approval capture, D known-at overlay replay and canonical artifact persistence remain separate integration gates; F stays a separate Draft PR. Optional price-candle v1.1 was not re-reviewed in this heartbeat.
