# P0 B/shared-v1 input binding to C

Issue3 marker: C-152823-B-e236-canonical-input-binding.

Exact reviewed dependencies (test snapshots under tests/analytics/reference/binding):

| Component | Pin | Actual code used in tests |
| --- | --- | --- |
| Existing C math | 152823e1528f0953f8175717212d795d7fe1ac0c | src/analytics/core.py, unchanged |
| B | e236395684d661f9464a62a0f6f67803d9000de6 | src/storage/models.py normalize |
| MASTER shared v1/projection | d945b23ab940b67f569ed85aa4c8d6e9684f8149 | contracts/v1 schema + validator; src/integration/storage_api.py project_trade/provenance_from_storage |
| E | b15b1ce12633a134b19cb71a57d7e841a7120579 | src/api/scope.py bound_response; src/api/canonical.py dataset_response |

The new module is `src.analytics.binding`. It performs input binding and metadata
validation only. Existing C deduplication, sorting, numeric and bar arithmetic is
called directly, unchanged. No B/E/MASTER production files or schemas are modified.

## B entry point

```python
from src.analytics.binding import bind_storage_trades

batch = bind_storage_trades(
    storage_rows,  # Store.read rows, each with exhaustive B provenance resolver
    venue='bybit', category='linear', symbol='BTCUSDT',
    start_ms=start_ms, end_ms=end_ms,
    information_cutoff_ms=cutoff_ms,
    quality_available_at_ms=quality_snapshot_available_at_ms,
    availability=resolved_availability, coverage=resolved_coverage,
    input_exhausted=all_storage_pages_drained,
)
value = batch.response('candles', interval='1', price_step='0.1', limit=query.limit)
```

Default ports import tester MASTER's `src.integration.storage_api.project_trade`
and `provenance_from_storage`. Missing ports fail DATA_UNAVAILABLE. The functions
may also be injected explicitly (as the exact pinned callables are in tests).
Shared `contracts.v1.validate` must be installed by integration. No foreign
repository/Gateway dependency is introduced.

B input must have authentic, exhaustive, nonlegacy observation linkage and
`source_run_status='matched'`, `authentic_run=True`, plus original source.ingestion_run
equal to stored run_id. Missing, undeclared, mismatched and unlinked runs fail
closed. Import acquisition kinds unsupported by the pinned MASTER projection
remain unsupported. C does not certify acquisition or coverage independently.

Every eligible source run is projected, not just the compatibility `source` field.
Actual record, observation, source, evidence and job IDs are retained as
`storage:<kind>:<original-id>` references in provenance.parent_refs. These are
references to existing B identities, not newly invented acquisitions. `batch.source_links`
returns a defensive-copy audit sidecar with full eligible resolver observations,
raw envelopes, original source fields, run IDs and evidence refs. This sidecar is
not an extra field in canonical output. Original inputs are never mutated.

## Canonical entry point

```python
from src.analytics.binding import bind_canonical_trades

batch = bind_canonical_trades(
    shared_v1_trades,
    venue='bybit', category='linear', symbol='BTCUSDT',
    start_ms=start_ms, end_ms=end_ms,
    information_cutoff_ms=cutoff_ms,
    available_at_ms=resolved_full_snapshot_available_at_ms,
    input_exhausted=all_input_pages_drained,
)
value = batch.response('footprint', interval='1', price_step='0.1', limit=query.limit)
```

`available_at_ms(record)` must return the known UTC epoch milliseconds when the
**entire supplied record snapshot**, including all source-run/provenance and quality
evidence, became available. Shared v1 has no available_at field, so it cannot be
silently added or inferred. Missing/invalid resolver times fail closed. Records
whose snapshots were unavailable at cutoff are excluded entirely; eligible
canonical provenance is retained unchanged.

## Cutoff, quality and request scope

- Event timestamps and information availability are separate. B uses original
  aware source.fetched_at for each observation; canonical snapshots require the
  explicit resolver above. No fallback to exchange timestamp or source_timestamp.
- B source.fetched_at supports RFC3339 seconds with up to 9 fractional digits and
  Z or numeric HH:MM offset. Fractional milliseconds round upward exactly using
  integer arithmetic. A fetch 1 ns after cutoff is excluded, never truncated early.
  Unsupported precision/format fails closed. Upstream B normalization may already
  have reduced precision; C cannot recover precision absent from the read record.
- B quality reports are explicitly supplied and attested available no later than
  cutoff. Their range must exactly match the request and pass shared validation.
  The integration owner must resolve B coverage/evidence/findings as of that time;
  this adapter does not authenticate caller assertions or construct COMPLETE.
- Unknown/partial/error/unavailable quality and original reasons/evidence survive.
  Exclusions and a sequence not explicitly attested exhausted can only weaken
  quality. Empty B snapshots retain upstream ERROR/GAPS/evidence where present;
  emptiness never becomes COMPLETE. No missing trades are filled.
- Source trade time must fall in [start_ms,end_ms), and market identity must match.
  Output ranges must align to full official UTC bars (including D/W/M). Closure
  uses the explicit cutoff. A footprint extending beyond cutoff cannot claim COMPLETE.
- `response` supports only canonical `candles` and time-total `footprint`, limit
  1..1000. It returns next_cursor=None only when the whole bounded result fits.
  More rows than the requested limit fails DATA_UNAVAILABLE; it never truncates
  input trades/outputs. Backend owns page draining and bounded bar-window selection.
  This is not an HTTP reader/pagination implementation.
- Price-level, statistics and custom-interval extensions remain explicit unsupported
  outputs at this boundary. No strict shared-v1 fields or enums are broadened.

## Reproducible verification and limits

Install test-only dependencies and run from the C checkout root:

```powershell
python -m pip install -r tests/analytics/requirements-binding.txt
python -m unittest discover -v
```

New tests use actual pinned B normalize, MASTER projection/shared validator and E
bound_response, not imitations. Scenarios include genuine ns/us ordering, extreme
Decimal strings, full-ID dedup/conflict, two acquisition runs and all storage refs,
UNKNOWN/partial source quality, unknown-side null delta, exact cutoff at +1 ns,
empty upstream ERROR, unclosed footprint, E output limits and calendar UTC grids.
All integration inputs are explicitly synthetic fixture identities/URLs; no actual
acquisition, PostgreSQL or HTTP service runs in this suite. Pydantic is required
only because the test executes E's real validation module. Production C arithmetic
continues using stdlib only.

This proves this in-process input/validation boundary for the stated pins only.
It does not claim full A/B/C/E integration, live-history completeness, current
unreviewed dependency compatibility or deployment readiness. No merge/deploy/trading.
