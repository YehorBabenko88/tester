# C analytics integration v1

P0 input-binding follow-up: [B/shared v1 input binding](INPUT_BINDING.md) documents
the new boundary, exact B/MASTER/E pins and additional test-only requirements.

Entry points (dictionary records; UTC epoch milliseconds):

```python
from src.analytics import normalize_trade, aggregate, footprint, compare_candles
trade = normalize_trade(raw, category='linear', symbol='BTCUSDT', source_ref=provenance)
candles = aggregate(trades, interval='1', price_step='0.1', as_of_ms=explicit_clock_ms)
taker_totals = footprint(trades, interval='1', price_step='0.1')
differences = compare_candles(candles, official, tolerance='0', rounding='none')
```

Canonical models are strict coordinator v1 records pinned to commit
9766c0093e2608280875ac56db074414a3d62c69. The four calls return canonical Trade,
Candle, footprint and comparison dictionaries, respectively. `source_ref` must be
a complete provenance mapping or list, not merely an URL. Source run/hash/precision
come from ingestion; C does not authenticate or certify B's evidence.

`as_of_ms` is explicit for reproducibility. Without it, candles remain unclosed;
closure never proves completeness. Full source coverage requires inherited evidence
covering the whole bucket. Empty input produces no records and proves nothing.

## Strict schema limitations and extension functions

Shared v1 candle forbids metrics; v1 footprint has no price bounds. Accordingly
`footprint` produces time-bucket taker totals. `price_levels(...)` returns an
analytics extension containing price_lower, price_upper, price_step, trade_count
and a strictly canonical child `footprint`. `candle_statistics(...)` returns a
separate extension with `quality` plus count, taker volumes, delta/CVD, VWAP,
profile, POC, value area, concentration, imbalance and large trades. These
extension wrappers are NOT shared v1 endpoint records. E must not silently send
them through the canonical candle/footprint endpoint. Schema extension request
is in Issue #3; no B/E/D/shared schema code is changed here.

`custom_candles(..., interval='45s', ...)` supports integer ms/s/m/h/d fixed widths
aligned to Unix epoch. It is an explicit extension because v1 candle interval enum
permits only official intervals. Official numeric intervals denote minutes;
D/W/M use calendar UTC midnight, Monday and month start. The epoch's first partial
week begins before zero and is rejected under the shared nonnegative timestamp
schema. No missing bars/trades are generated.

## Source semantics and identity

Bybit REST [recent-trade](https://bybit-exchange.github.io/docs/v5/market/recent-trade)
documents execId, price, size, side of taker and millisecond time. Archive CSV
timestamp is parsed as decimal seconds with caller-declared source precision;
no binary float or manufactured ns. Genuine us/ns source timestamps retain the
nanosecond integer string with matching timestamp_ms floor. Raw original source
timestamp and parent references remain in provenance.

The public [archive portal](https://www.bybit.com/en/derivative-activity/history-data)
and official archive bytes establish origin, but REST's side description alone
does not establish archive-side semantics. Raw `bybit_archive` side is therefore
normalized to Unknown. A/B may provide already verified canonical Trade records
once archive semantics are independently established. Synthetic fixtures specify
their own semantics explicitly. No record asserts verified Bid/Ask execution.

Identity is `(venue,category,symbol,trade_id)`. Equivalent finite decimal spellings
normalize identically. Changed economic content for a stable key raises
`SourceConflict`, never overwrites. Duplicate provenance is deterministically
unioned, weaker quality retained. Missing IDs use a documented SHA256 composite
with id_origin=synthetic; identical executions can collide, so coverage is
UNVERIFIED/UNKNOWN and cannot prove completeness. Prefer original exchange IDs.

Order is market, genuine source ns if available (otherwise ms), then lexical
trade_id. Equal timestamps without exchange sequencing cannot recover true
execution order: the tie rule is deterministic and must not be presented as
verified exchange sequence. This can affect observed open/close comparisons.

## Formulae and units

Financial inputs are Decimal strings. Exact Fraction representations of Decimal
values avoid loss from ambient Decimal precision for sums/products/buckets.
Finite decimal results are constructed exactly as Decimal tuples. Repeating
ratios and transcendental statistics use 50 significant digits, HALF_EVEN,
independent of the caller's context. JSON output uses plain decimal strings.

For quantity q and price p: linear/spot base volume=q, quote notional=p*q;
inverse quote notional=q, base quantity=q/p. `trade_notional` exposes explicit
base/quote units separately because shared Trade forbids notional fields.
[Official kline units](https://bybit-exchange.github.io/docs/v5/market/kline)
confirm the volume/turnover distinction. Coin labels require instrument metadata.

OHLC follows sorted observed executions; volume=sum(q), count=unique trade count.
Buy=sum(q|taker Buy); Sell=sum(q|taker Sell); delta=Buy-Sell only if Unknown=0.
CVD is sum(delta) across observed buckets per market from the first supplied
bucket. Any unknown side or missing time bucket makes this and subsequent CVD
null, with reason; no reset or fabricated flow. Source availability/coverage
remains visible, so observed sums never imply full historical totals.

VWAP=sum(quote notional)/sum(base quantity), including correct inverse weighting.
Price bucket=[floor(p/step)*step, lower+step). Profile=sum(q) per price bucket.
POC=max volume, tie→lower price. Value area starts at POC and adds the larger
adjacent occupied price bucket until >=fraction*volume (default 0.7); tie→lower.
Its interval includes any empty levels between occupied bounds.
Volume and level imbalance=(Buy-Sell)/(Buy+Sell), null for unknown side.
Buy/Sell ratio is null when denominator zero, with reason.
Large trade means q>=explicit threshold in category quantity units; report count,
sum(q), maximum. Without threshold the count is zero and threshold=null (disabled).
Traded-volume concentration=sum((level_volume/total_volume)^2), an HHI of observed
executed volume, NOT an order-book liquidity measurement. No historical resting
liquidity or market capitalization is inferred from turnover.

`src.analytics.indicators` supplies descriptive extensions with inherited quality:

- Log return r=ln(close_t/close_prev). Realized volatility=sqrt(sum(r²)), unannualized.
- Historical volatility=sample_stddev(r)*sqrt(explicit periods_per_year), ddof=1.
- ATR uses max(H-L,abs(H-prevClose),abs(L-prevClose)); seed mean of period true
  ranges, then Wilder recurrence ((period-1)*ATR+TR)/period. No previous close
  means no true range; insufficient history returns null and reason.
- Volume anomaly z=(current-mean(prior window))/population_std(prior window).
  Current observation is excluded from baseline; insufficient/zero variance→null.
- OI change=OI_t-OI_prev; relative=change/OI_prev, null at zero denominator.
  OI/price divergence means opposite signs of changes, aligned at exact timestamps,
  never forward-filled. Missing OI downgrades derived quality. Units cannot mix.
  [Official OI](https://bybit-exchange.github.io/docs/v5/market/open-interest)
  distinguishes inverse quote and linear base quantities.
- Funding dynamics: observed sum/mean and last difference, never implicit APR.
  Funding rates are not realized portfolio PnL.
- Correlation: Pearson correlation of exact timestamp-pair-aligned log returns;
  fewer than two pairs or zero variance→null. Candle gaps/mixed identity reject.
- Returns distribution: log returns, mean, population stddev, min/max,
  nearest-rank 5/50/95 percentiles, population skew and excess kurtosis.
- 1h/24h rankings: descending unannualized realized volatility of contiguous,
  closed, COMPLETE/AVAILABLE candles in the full UTC lookback; unsupported or
  incomplete windows stay null with reason. Returns use closes inside the window.

## Comparison and reproducible checks

`compare_candles` compares OHLCV only against `official_trade`, never mark/index.
MATCH/MISMATCH requires complete evidence, closed candles, equal UTC interval
boundaries and explicit nonnegative absolute tolerance plus rounding='none'.
Otherwise return NOT_COMPARABLE (or missing-side status) and reason. Returned
differences remain visible even when verdict is not comparable. Bybit kline has
no taker delta, so no official delta is invented; synthetic independently computed
Buy/Sell/Delta fixtures exercise that arithmetic.

```powershell
python -m unittest discover -v
python -m tests.analytics.reproduce_initial  # expected exit 1, two baseline failures
python docs/analytics/run_official_comparison.py  # opt-in network, bounded <5 MB
python docs/analytics/run_official_comparison.py --offline  # replay saved source snapshots
```

Tests need `jsonschema>=4.18` to run the exact shared validator snapshot; production
analytics uses only Python stdlib. All unit fixtures are explicitly synthetic.
Reference schema/validator copies are unchanged from pinned coordinator SHA;
initial_footprint.py is unchanged from C 0070b5fcb9180a79b213698c99ea289df4442383.

Live evidence in evidence/ is separate: official XRPUSDT archive (43210 daily rows,
2938 selected first-hour trades) and REST 60 one-minute candles. 300 observed OHLCV
field comparisons show 27 nonzero differences (23 opens, one high, three lows);
all 60 volumes agree. The report preserves each difference and archive hash.
These differences are unresolved, not repaired with invented executions or carried
prices. No causal explanation or full completeness claim is made. Gated verdict
is NOT_COMPARABLE; raw archive delta is unavailable pending verified side semantics.
This is not live A/B/E end-to-end acceptance or proof of B storage integration.
