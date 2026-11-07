# Financial query request contract 1.0

This request version is separate from immutable shared market records 1.0. The
record schema and C arithmetic are unchanged. This is an explicit-input transport
boundary, not activation of a production analytics/storage adapter.

For `/api/footprint` and `/api/candles?candle_kind=derived`, supply:

- `query_schema_version=1.0`, `symbol`, explicit `start_ms` and `end_ms`.
- `information_cutoff_ms`: inclusive nonnegative integer epoch milliseconds,
  maximum 9007199254740991. HTTP accepts decimal integer digits only.
- `price_step_mode=explicit` and `price_step`: positive plain decimal **string**,
  at most 256 characters. No float, exponent, sign, whitespace, zero or NaN.

Example: `/api/footprint?symbol=BTCUSDT&start_ms=0&end_ms=60000&interval=1&query_schema_version=1.0&information_cutoff_ms=60000&price_step_mode=explicit&price_step=0.10`.
This example describes synthetic historical time, not available market data.

`explicit` means a researcher-selected aggregation grid. It makes no assertion
that the selected step equals an exchange tick. The reserved selection
`price_step_mode=instrument_tick` requires both an explicit exact `price_step`
and an immutable `instrument_tick_ref`. It currently returns 503
`instrument_tick_unavailable`, even if a reader is configured. There is no tick
resolver or fallback to current instrument metadata. An eventual resolver must
prove instrument identity, effective time, availability time and immutable
metadata revision before checking the submitted exact step.

There are no new current-time, cutoff or price-step defaults. Ordinary current
views and research views must both freeze an explicit information boundary before
requesting derived data. Proposed future defaults require a separate reviewed
contract and snapshot policy; none are enabled here. These financial fields on
other resources, including official candles, return 422 rather than being ignored.

Event range is `[start_ms,end_ms)`. Cutoff is a separate information boundary,
not the requested event end or a wall-clock read. It must never be substituted for
`source.fetched_at`, a run timestamp or `quality_available_at_ms`. The integration
owner must provide the actual independent quality availability and original
observation acquisition times. Canonical record 1.0 has no general historical
available-at field; transport validation alone cannot certify historical absence
of look-ahead. Never infer it from candle time, trade time or request time.

The typed `FinancialReader.read_derived(resource, FinancialRequest)` port receives
a frozen normalized snapshot and decoded source cursor. Generic `Reader.read`
cannot serve derived requests. The default reader has no derived port and returns
503 `derived_provider_unavailable` after valid explicit inputs. Missing inputs
return 422 `financial_query_required`. Conflicting selection returns 422
`price_step_selection_invalid`. Response schema and scope validation still run.

HMAC continuation tokens bind request version, inclusive cutoff, the **lexical**
step string (`0.10` differs from `0.1`), selection mode and instrument revision,
in addition to existing resource/scope/limit filters. Changing any of these or
restarting the API invalidates continuation. This protects request immutability;
it does not provide a cross-page database snapshot. A future owned adapter must
retain a stable bounded full-range input and prove consistent pages separately.

The B `Store.read_page` proposal at dc545c0a supplies a per-call repeatable-read
bounded proof, not a cross-page snapshot. Before activation, the integration owner
must review `outcome == OK`, `coverage.complete`, nonempty proof references,
current active proof, resource bounds, source/run linkage, historical quality
time and full-range exhaustion. Partial or unavailable proof cannot become
COMPLETE. No adapter or native tick selection is activated by this change.

Tests use byte-pinned test-only B normalization, MASTER projection and C binding
from coordinator 19f306cd. They execute actual C aggregation via the HTTP port,
including original nanosecond acquisition boundaries and independently fixed
quality time. UNKNOWN synthetic quality remains UNKNOWN. Test-only reference
modules are excluded from wheel packaging and checked by SHA256; E contains no
production copy of analytics math. The transport pagination fixture deliberately
uses a synthetic cursor and makes no B full-range or database snapshot claim.

Machine-readable frozen provider snapshot:
`contracts/requests/v1/financial-query.schema.json`.
Public query parameters are in `contracts/v1/openapi.json`.
Regenerate both with `python scripts/export_contract.py`; CI checks drift.
