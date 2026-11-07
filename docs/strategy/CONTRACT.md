# Strategy Lab contract — initial design for Agent F

Scope: read-only historical research and simulated orders ONLY. Not a connection to an exchange order endpoint.

## Interfaces

`POST /api/strategies/validate`: parse a versioned declarative JSON DSL, reject unsupported operators and missing risk assumptions. Natural-language strategy text must first yield a preview with explicit unresolved assumptions, not execute Python.

`POST /api/backtests`: submit {strategy_version, strategy, market_snapshot, [start_ms,end_ms), timeframe, initial_equity, fees, slippage, funding_policy, fill_policy, gap_policy, seed}. Requires completed validation and data-quality gate. Returns job id.

`GET /api/backtests/{id}`: progress, proof of snapshot/version, warnings, status.
`GET /api/backtests/{id}/results`: trades, simulated orders/fills, equity, drawdown, scorecard, data-quality annotations, provenance.
`GET /api/backtests/{id}/overlays`: paged chart markers/levels/SL/TP, bounded UTC ranges and revision pinning.

All results are deterministic for pinned input bytes and engine version, with Decimal strings in JSON. Simulation cannot produce real orders, and any provider reuse must be read-only.

## Non-negotiable semantics

- No lookahead: indicators and signals only use closed observations available at decision time; trades/fills execute on following eligible price evidence, with configurable latency.
- Missing/partial archives: fail closed by default; optional explicit exploratory mode clearly marked NON-CERTIFIED.
- If both stop and target are touched in one OHLC bar, do not optimistically assume target first. Default conservative outcome or declare ambiguous; user can choose verified lower timeframe/tick reconstruction.
- Fees/spread/slippage/funding/margin/liquidation must be explicitly modeled or reported unavailable, not treated as free.
- Timeframe-aware support/resistance criteria must define pivot detection width, allowed touch distance, closing-break threshold, lookback six calendar months vs fixed days, confirmation delay, invalidation and time-of-knowledge.
- Prevent parameter overfit: chronological train/validation/test, walk-forward, metrics by sample and a disclosure that historical performance doesn't predict future returns.
- Persist runs and immutable strategy configuration, market source/evidence references, software versions, exact fill events and replay digests.
- Every plotted entry/exit must correspond to a recorded fill and vice versa; never plot imagined execution.

## Owned modules

Agent F owns `src/strategy/`, `src/backtest/`, `tests/strategy/`, `docs/strategy/`; E owns API integration, D owns UI overlays, C owns shared market analytics, B owns persistent database, A owns acquisition. No direct cross-workstream changes.
