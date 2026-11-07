# Numerical and event semantics — engine strategy-bar-1.1.0

All financial arithmetic uses a local Decimal context with precision 50. Unit sizes
are floored to `quantity_step`; there is no arbitrary cent rounding. Fee rates and
risk distances are fractions, e.g. 0.001=0.1%, 0.05=5%; spread/slippage are bps.

## Event order and evidence

1. At observed bar open: cancel stale untriggered orders if a data gap occurred; clear
   indicator windows. Carry existing positions across gaps with NON_CERTIFIED warning.
   Preserve already-triggered protective market residuals across gaps.
2. Apply funding to positions held immediately before this boundary. New entries do
   not collect/pay that already-due funding event.
3. Evaluate open-price protective triggers. An open beyond TP executes the resting
   target without favorable improvement, before later intrabar movements.
4. Execute eligible pending market exits/entries at open; resting limit/stop entries
   may instead use that bar's range. Latency 1 means next bar, never signal-bar fill.
5. Evaluate remaining bar range for protection. If adverse and favorable barriers
   both touch and open has not resolved order, adverse stop wins. An intrabar entry
   may suffer a stop in the same bar but never receives a same-bar target. This
   pessimism is intentional; it does not assert the unobserved tick path. Whole-bar
   capacity-dependent entries are also timing-uncertain and cannot collect same-bar TP.
6. At close: mark equity; evaluate closed-bar rules; schedule future orders. Trailing
   stop ratchets from this close and activates on the following bar, never from the
   future high/low. Record SL/TP/trailing snapshots for chart integration.

Market buy fill = reference * (1 + slippage_bps/10000 + spread_bps/20000), sell uses
minus. Explicit zero costs are a user assumption, not measured free execution.
Spread is fixed configured full spread; observed quote reconstruction is not
implemented. Limit fills cap modeled buy price at limit, floor sell at limit.
Buy limits touched by low fill at min(open,limit); sell mirror. Buy stops touched
by high fill at max(open,stop); sell mirror. Adverse gap SL uses actual open.
Bar range is an execution eligibility bound, not evidence of available queue volume.

An entry's full requested notional must satisfy margin+fee before any participation
cap is applied. An unaffordable oversized request is rejected even if its eventual
partial fill might be affordable. IOC on first touched eligible bar: filled fraction
opens a position, entry remainder is cancelled. Limit/stop wait until touched;
zero capacity cancels the touched entry. Exit residuals persist across bars, sharing
bar capacity with other fills; the same residual order is reused. A protective
stop/target/liquidation trigger latches a reduce-only market exit before sizing,
including zero executable quantity. It keeps its reason and order identity through
rebounds and gaps, and executes at the next available reference price; a rebound
does not restore its old untriggered bracket. Failed-to-fill protection is not
fabricated as executed. No queue priority, tick-path reconstruction, or true exchange
matching behavior is claimed.

Whole-bar capacity is admitted only with a complete quality/provenance envelope,
matching [start_ms,end_ms) and available_at_ms=end_ms. Late, premature, incomplete
or unverified evidence is rejected. The bar is processed to completion as one step;
its capacity quantity is never claimed to have been known at its opening. All fills
using that budget have time_precision=bar, time_window=[start_ms,end_ms),
known_at_ms=end_ms and representative timestamp_ms=end_ms-1. A price may still use
the modeled open/range reference described above, but does not assert liquidity
or execution at that reference time. Overlay replay must wait for known_at_ms.
The budget is consumed across all fills and cannot be spent twice in a bar. Without
a participation cap, open-reference fills retain modeled_open and open known_at_ms;
range-based fills remain uncertain/known at close.

Funding cost = side_sign * quantity * settlement_mark * funding_rate; a negative
cost is a receipt. Cash wallet changes only by realized PnL, fees and funding.
Principal is reserved as margin, not subtracted a second time. Free initial buying
power uses cash/(price*(1/leverage+fee_rate)); the spot wallet is this fully reserved
research ledger, not an exchange balance API. Only one position per independent run.
Spot is Long/unlevered; linear supports Long/Short and leverage.

Equity = cash + sign * q * (close-entry). Margin = q*entry/leverage.
Simplified cross-wallet liquidation threshold solves equity = maintenance*q*price:
Long: (q*entry-cash)/(q*(1-maintenance)); Short: (cash+q*entry)/(q*(1+maintenance)).
The closer adverse SL/trailing/liquidation boundary wins; a gap can skip it. Funding
and fees affect wallet equity. Gap loss may exceed initial equity; no invented
insurance, ADL, exchange tier, liquidation fee or bankruptcy cap is applied. These
unavailable details are explicitly flagged LIQUIDATION_MODEL_SIMPLIFIED.

## Indicators and metrics

SMA: mean of final p field observations. Finite rolling EMA: initialize at first of
p observations then alpha=2/(p+1); this is not an infinite-history EMA. RSI uses p
simple gains/losses from p+1 closes (not Wilder smoothing); flat=50, no loss=100.
Rolling VWAP = sum(field*base_volume)/sum(base_volume), default field=close; zero
volume means unavailable. C's trade-derived VWAP may be used as a feature instead.
Crosses require both operands available on previous and current closed observations.
Doji: abs(close-open) <=0.1*(high-low); engulfing uses full previous body inclusively.

Realized exit-leg gross PnL = sign * exit_qty * (exit-entry).
Net = gross - proportional entry fee - exit fee - allocated funding.
Each partial exit is a realized leg in `trades`; trade_count, win_rate, profit_factor
and expectancy are LEG statistics, not round-trip-position statistics. IDs retain
the common entry_fill_id so B/D may group positions without inventing executions.
Win rate = profitable legs / legs; PF = sum(positive net)/abs(sum(negative net));
PF=null when denominator zero, not infinity. Expectancy = sum(net)/leg_count;
no-leg win rate/expectancy=null. Total fees include open-position entry fees.
Exposure sums whole observed bars with a position except known open-only exits;
intrabar timing is unknown, so this is an upper bound on observed-bar exposure,
not time in an unobserved gap. Denominator includes elapsed gaps; no synthetic
equity points are inserted. Drawdown = max((running_peak-equity)/running_peak)
on observed close marks; equity can go below zero, drawdown can exceed 1.
No confidence interval is invented for dependent or selected historical outcomes.

## Six-month unbroken level definition

Default lookback subtracts six calendar months in UTC, clamping day-of-month (Aug31
2024 -> Feb29 2024). Optional fixed window_days is explicitly different. A pivot high
at j must be STRICTLY greater than each of left neighbors and right neighbors; low
mirrors. Equal extrema produce no pivot. It becomes known only at close of j+right;
the output includes both pivot_ms and known_at_ms. Nothing is backdated for signals.

Each pivot starts a separate level with one touch; no future clustering or merging.
Tolerance distance d=level_price*tolerance_bps/10000. A later bar touches iff its
range intersects [level-d,level+d] and it is not a breaking bar. Consecutive bars
count as distinct touches. Prior bars in the pivot's right confirmation window
are not counted retrospectively. A level is confirmed when touch_count>=min_touches.
Unbroken means no confirmed break since that pivot and not expired/gap-invalidated.

Resistance break: close>level+d (or high>level+d in wick mode); support uses <level-d
on close/low. Require confirmation_bars consecutive observations. Breakout emits
invalidated and removes the level from active unbroken levels. Within retest_bars
after break, a touching bar that closes on the broken side emits one retest; a close
beyond the opposite tolerance edge emits false_breakout. False-breakout invalidates
that level permanently; it is not silently restored as unbroken. Expiration uses
pivot time, not the latest touch. Gaps invalidate all levels and reset pivot windows.
Push only closed bars in order; the snapshot adapter owns quality/provenance checks.

## Replay and artifacts

Checkpoint stores version, input hash, next bar index and prefix digest. Restore
replays the immutable prefix and verifies its digest, O(processed bars); it does not
deserialize executable state. Input or engine changes reject old checkpoints. Run
ID = SHA256(canonical JSON of strategy SHA, market SHA, engine version). Canonical
JSON sorts keys with compact separators; Decimal strings preserve input scale.
Semantically equal differently scaled inputs can intentionally have different IDs.
No wall-clock time or global RNG enters the computation.
