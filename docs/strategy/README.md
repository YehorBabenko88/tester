# Historical Strategy Lab

Offline simulation only. Python >=3.12, standard library, no exchange client or
order transport. F owns only `src/strategy`, `src/backtest`, `tests/strategy`,
`docs/strategy`. Existing application files are untouched.

```python
from src.strategy import validate_strategy
from src.strategy.text import compile_text
from src.backtest import Backtest, run, restore
from src.backtest.artifacts import save_artifact

strategy = validate_strategy(configuration)  # ordinary JSON object
preview = compile_text('long when close > 99', strategy)
# E displays preview.strategy + preview.assumptions, obtains user confirmation,
# then calls confirm_preview(preview, preview['preview_id']). No auto-confirmation.
result = run(strategy, market_snapshot)
artifact_path = save_artifact(result, strategy, market_snapshot, research_directory)
```

`strategy.schema.json` is the structural JSON Schema 2020-12 contract. The runtime
validator also enforces Decimal numeric bounds, depth <=16, <=256 expression nodes,
period/offset <=10000, no unknown properties and no non-JSON types. Prices, rates,
quantities, fees and results are plain Decimal strings. Integers represent counters,
UTC epoch milliseconds and bar latency. Money never round-trips through float.

## DSL

See `example.strategy.json` for a runnable configuration. Entry has `long` and/or
`short` expressions. Exit is evaluated for the current position. A simultaneous
Long/Short signal is recorded as a conflict and does not create an order.

Expressions: `and`/`or` with `args`; `gt/gte/lt/lte/eq/cross_above/cross_below`
with `left`/`right`; `pattern` with `bullish`, `bearish`, `bullish_engulfing`,
`bearish_engulfing`, `doji`; `session` with UTC start/end minute and weekdays
(Monday=0). Overnight sessions use the weekday at decision time. Equal session
boundaries mean the whole day. Offsets are nonnegative historical offsets.

Operands: `{"value":"100"}`, `{"field":"close","offset":1}`,
`{"indicator":"sma","period":20}`, or `{"feature":"delta"}`. OHLCV fields
support price action and volume rules. `delta`, `cvd`, `oi`, `funding`, `vwap`,
support/resistance and arbitrary C analytical identifiers use the feature route.
Missing, PARTIAL or late features never satisfy a condition; no implicit zeros or
forward-filling. The finite-window strategy indicators are independent F helpers,
not changes to C's shared indicators. Their precise definitions are in FORMULAS.md.

Text compilation is intentionally restricted to `long|short when FIELD OP NUMBER`,
using an explicit full base configuration. Unknown prose yields an unresolved
preview with no runnable strategy. An E-side language translator can call
`preview_text(text, candidate, assumptions)` for broader language support. It must
display the whole candidate and obtain confirmation; this library does not claim
general natural-language understanding. A SHA-pinned preview becomes invalid if
text, assumptions or configuration change. Confirmation is an application workflow
requirement, not an authentication token; E must record the user's actual approval.

## Historical snapshot / integration boundary

The fixture `example.snapshot.json` is synthetic, explicitly NON_CERTIFIED. It is
an integration example, not evidence of profitable performance or live coverage.

Snapshot required keys: `schema_version='1.0'`, `venue='bybit'`, `category` (`spot`
or `linear`), `symbol`, `[start_ms,end_ms)`, fixed `timeframe_ms`, `initial_equity`,
`quantity_step`, `candles`, `availability`, `coverage`, `provenance`, `funding`.
Candles retain approved #8 fields without renaming. Only closed trade/derived candles
are accepted, not index/mark/premium OHLC as executable prices. Fixed intervals
1..720/D/W are supported; calendar-month OHLC and inverse payoff are rejected.

Each candle may add F envelope extensions `features` and `liquidity`. E must remove
these two extensions before passing the canonical candle to MASTER's approved
`contracts/v1/validate.py::validate_record('candle', candle)`. F's independent gate
checks identity, decimal OHLC consistency, sorted unique intervals, closure,
provenance, availability, evidence references and actual gaps; it is not a substitute
for the full shared semantic validator. No normalization implies completeness.
Source/evidence labels supplied by A/B/C are trusted assertions, not independently
reverified archive contents. A real-data conformance smoke remains an integration gate.

Feature envelope: `{value: Decimal-string, available_at_ms: integer, availability,
coverage, provenance}`. Features belong to the containing bar, use only versions
actually available at its close, and inherit full source/coverage evidence. Late
versions remain unavailable for that historical decision. C/E must not inject
retrospectively revised values with an invented earlier `available_at_ms`.

Liquidity extension (engine 1.1): `{quantity: Decimal-string, start_ms, end_ms,
available_at_ms, evidence_refs: [source IDs], availability, coverage, provenance}`
is a whole-bar execution-volume capacity bound from verified finer evidence. Its
range must equal the containing bar and `available_at_ms` must equal its close.
Premature claims about a whole bar, evidence arriving after the decision close,
PARTIAL/UNAVAILABLE evidence and legacy unqualified quantities are rejected, including
in exploratory mode. With `max_participation` all bars require this complete evidence;
OHLCV volume alone is not promoted to queue liquidity.

Capacity is consumed across all fills in that bar. Every capacity-dependent fill is
explicitly uncertain within `time_window`, has `time_precision='bar'`, representative
`timestamp_ms=end_ms-1` and `known_at_ms=end_ms`, even if its price reference is the
open and even if its capacity bound does not bind. The result carries
`BAR_CAPACITY_TIMING_UNCERTAIN`; these fills do not establish opening liquidity or
tick ordering. Capacity-dependent entries cannot collect a same-bar target. This is
a conservative bar participation model, not finer-time execution reconstruction.

Funding for linear contracts must be `required` or explicitly `ignore_exploratory`
(NON_CERTIFIED). Required funding is `{availability, coverage, provenance,
expected_timestamps, events:[{timestamp_ms, rate, mark_price}]}`. Events must exactly
match the supplied complete schedule and observed bar boundaries. Intrabar funding
without finer timing evidence is rejected. A complete empty schedule is legitimate
only if upstream evidence establishes no settlement during that interval.

## Ownership handoff

- E: `validate_strategy`, `compile_text`/`preview_text`/`confirm_preview`,
  `Backtest.step/checkpoint/result`, `restore`, `run`. HTTP routing/auth/job queues
  are not added here. Cancel by retaining a checkpoint; resume deterministically.
- C: provide point-in-time feature envelopes. `LevelTracker.push(closed_bar)`
  supplies F-owned delayed level events; wrap its numeric features with real
  derivation provenance before strategy evaluation. No derived metadata is fabricated.
- D: `result.overlays` is exactly one marker per fill; join on `fill_id` and `order_id`.
  For `time_precision='bar'`, draw uncertainty across `time_window`, not a precise
  tick execution. The last-ms timestamp is a representative marker, not a measured
  execution time. In replay, reveal fills and markers only at `known_at_ms`; never
  reinterpret the containing interval's start as their availability. `brackets`
  contains risk snapshots; tracker events use `known_at_ms`
  for first visibility, not `pivot_ms`. E handles range paging and revision pins.
- B: store portable artifact payload or project it into canonical PostgreSQL.
  Artifacts include full strategy, full snapshot, result and SHA-256 integrity.
  F does not create or mutate a database. An artifact hash detects accidental changes,
  not malicious edits by someone who can recompute hashes.

Results pin strategy ID/version, strategy SHA, snapshot SHA, engine version, run ID,
replay digest, sources, quality, orders, fills, signals, exit legs, risk, equity and
metrics. Open positions remain marked to final close; they are not fictitiously
liquidated at the end. Orders beyond the final eligible bar are cancelled.
Triggered protective exits remain pending even if no quantity can fill, and survive
price rebounds and data gaps until later eligible evidence (or end-of-run cancellation).
See MIGRATIONS.md for the 1.0 -> 1.1 evidence/checkpoint compatibility boundary.

## Reproducible research

`grid_search(strategy,snapshot,grid,seed=0,max_candidates=256)` uses existing scalar
dotted paths and deterministic ordering. It is explicitly in-sample exploration.
`optimize(...,train_bars,validation_bars,shortlist=3)` ranks training results, selects
from a fixed validation shortlist and evaluates only the selected configuration on
the untouched test segment. Each segment starts flat with fresh capital and no
indicator warm-up borrowed from another segment. Equity return includes final open
mark-to-market, so it differs from realized trade expectancy.

`walk_forward(...,train_bars,validation_bars,test_bars)` rolls by test length; test
windows do not overlap. Later training can include earlier test periods, as it would
in chronological deployment. Fold capital resets; returns are not presented as a
stitched investable portfolio. Report fold range/positive count, selected-parameter
changes and validation sensitivity. Seed is recorded; no stochastic search or fills
are used. Candidate budgets, immutable holdouts and explicit disclosures reduce
selection errors, but cannot prevent a caller from repeatedly optimizing against
the same test history. Statistical confidence is explicitly not estimated for
dependent selected trades. Historical profits do not guarantee future profits.

## Verification

```text
python -m unittest discover -s tests/strategy -p "test_*.py" -v
python -m unittest discover -p "test_*.py" -v
python -m compileall -q src tests/strategy
```

Run the strategy command explicitly: the existing root discovery on Python 3.12 does
not traverse namespace-only `tests/`. F has left other owners' CI files unchanged;
MASTER must add this command to integration CI. See TEST_REPORT.md for observed runs.
