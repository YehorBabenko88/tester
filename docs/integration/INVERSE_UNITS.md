# Execution quantity units: spot, linear, inverse (DATA -> RESEARCH -> UI)

Status: implemented on `feat/inverse-data-research-20261125`. Inverse executions
now flow end to end through the **same** canonical path as linear:
`public_auxiliary.fetch_and_ingest` -> `Store.ingest` (tester_storage, no new
migration) -> `execution_contract.read_execution_events` / `to_research_trade` /
`read_research_trades` -> `data_research_adapter.read_data_metrics` ->
`research_metrics.calculate_v2` (`tester.execution-metrics/2`) ->
`research_ui_projection.read_verified_ui_observation` and the
`/api/capabilities` probe (`capability_data_bridge`). Historical
`tester.execution-metrics/1` results stay verifiable read-only and unchanged; /1
inverse stays refused; new results are written only as /2.

## Source facts (Bybit V5, public)

- Kline `volume`: "USDT or USDC contract: unit is base coin"; "Inverse contract:
  unit is quote coin (e.g., USD)". Kline `turnover`: linear = quote coin,
  inverse = base coin (BTC). Source: Bybit V5 docs `/v5/market/kline`.
- Inverse contracts are quote-denominated: Bybit inverse FAQ gives
  "Position value = Quantity (size) / Mark price", i.e. one contract = 1 USD
  (quote). TESTER therefore treats the inverse `size` as a count of contracts
  with a fixed value of 1 quote unit each. No other multiplier is assumed or
  applied; a different contract value would be a new contract version.
- `/v5/market/recent-trade` documents `size` only as "Trade size"; its unit is
  taken to be the same as the kline volume unit of the category (above).
- spot recent-trade is allowed by `public_access` but has **no** DATA execution
  producer (`public_auxiliary` emits linear/inverse only). Spot is refused
  fail-closed before any SQL by the execution contract, adapter and probe.

## Units per category and layer

| Layer | spot | linear | inverse |
|---|---|---|---|
| Bybit trade `size` | base | base | contracts (1 USD each = quote) |
| storage payload `unit` (public_auxiliary) | - (no producer) | `source_native_linear_size` | `source_native_inverse_contract` |
| execution event `quantity_unit` (`SOURCE_QUANTITY_UNITS`) | refused | `source_native_linear_size` | `source_native_inverse_contract` |
| canonical trade `quantity` (analytics core) | base | base | contracts; unit fixed by `category` via `core.QUANTITY_UNITS` |
| /2 metrics `quantity_unit` | `base` | `base` | `quote` |
| volume, buy/sell/unknown volume, delta, CVD, footprint levels, POC volume | base | base | contracts (= quote) |
| VWAP (`vwap_unit` = quote per base) | sum(p*q)/sum(q) | sum(p*q)/sum(q) | sum(q)/sum(q/p) |
| `core.trade_notional` value / base_quantity | p*q / q | p*q / q | q / q/p |
| `statistics.candle_statistics` base_volume / quote_notional | sum q / sum p*q | sum q / sum p*q | sum q/p / sum q |
| preview `quantity_unit` (source) | refused | `source_native_linear_size` | `source_native_inverse_contract` |
| UI observation `quantity_unit` / `source_quantity_unit` / `vwap_unit` | (preview only via research_preview) | `base` / source unit / `quote_per_base` | `quote` / source unit / `quote_per_base` |
| capability probe `quantity_unit` | `execution_category_unit_unsupported` | `source_native_linear_size` | `source_native_inverse_contract` |
| research_preview (B candle snapshot) | yes (`base`) | yes (`base`) | refused (`SnapshotRequest` is the B->F seam; F has no inverse PnL) |

Worked example (asserted exactly in `test_runtime_privileges_issue114.py` as the
restricted `tester_app` role, and in `test_inverse_units_canonical.py`):
100 @ 100 taker Buy + 100 @ 200 taker Sell.

- inverse: volume 200 contracts, buy 100, sell 100, delta 0, CVD 0, footprint
  100: buy 100 / 200: sell 100, VWAP 200 / (1 + 1/2) = 400/3
  (serialized by `exact.decimal` to 50 significant digits).
- linear / spot: volume 200 base, same per-side split, VWAP 30000/200 = 150.

## Rules

- Never mix base quantity, quote quantity and contract counts. The base amount
  of an inverse trade (q/p) exists only inside the VWAP denominator and
  `trade_notional.base_quantity`; volumes are never converted.
- Mixed-unit aggregation is refused: `calculate_v2` raises
  `mixed market identities` for two categories (even with the same symbol
  name); core footprint/candles/statistics group per (venue, category, symbol);
  `combine_observations` labels `quantity_units` / `mixed_quantity_units` and
  never sums.
- DATA and RESEARCH unit tables must agree: `read_data_metrics` refuses a
  non-linear category unless DATA declares it in `RESEARCH_CATEGORIES` with the
  identical `SOURCE_QUANTITY_UNITS` entry (`data_source_quantity_unit_skew`);
  the probe refuses producer skew (`data72_quantity_unit_skew`).
- Bounded reads are unchanged for every category (100 rows, 7 days, READ_BUDGET,
  full page -> `requires_pagination`).

## Interface for the GAP-5 tick-vs-candle volume reconciliation (agent J)

Use `src.analytics.core.QUANTITY_UNITS` / `quantity_unit(category)` as the unit
authority. Compare per (venue, category, symbol, candle window):

- sum of canonical trade `quantity` <-> kline `volume` (both source-native:
  base for spot/linear, contracts = quote for inverse);
- linear/spot: sum(p*q) <-> kline `turnover` (quote);
  inverse: sum(q/p) <-> kline `turnover` (base coin).

Use `core.trade_notional(t)` for per-trade quote/base amounts instead of
re-deriving them. Never reconcile across categories.

## Other producers

- Archive trades (`archive.iter_trade_records`) keep the original CSV row as
  payload without a `unit` key and are `bybit_archive` sourced, so the
  execution contract (REST recent-trade proof only) never reads them; their unit
  is implied by `category` through `core.QUANTITY_UNITS`. Their inverse column
  semantics were not re-verified against a live archive file in this change.
- `handoff.contract_record` maps trades to `quantity=payload.size` without
  conversion (unit implied by category); unchanged.
