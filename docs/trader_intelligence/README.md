# Runnable trader research

H continues the existing normalizer. All exchange order/account interfaces remain absent.

## Local scenario

Use an existing isolated tester PostgreSQL database and existing repository Python
dependencies. No service registration or machine installation is required.

```powershell
$env:TESTER_H_DSN='host=127.0.0.1 port=YOUR_PORT user=YOUR_USER dbname=YOUR_TESTER_DB'
python -m src.trader_intelligence.demo --output research-result.json
streamlit run research_app.py --server.address 127.0.0.1
```

CLI: synthetic authorized export -> immutable snapshot -> selected disclosed event ->
plan -> synthetic-only approval -> reconstruction -> persisted digest-backed artifact.
The Streamlit workspace adds explicit plan approval and plot of disclosed execution
prices. It does not download market history, invent candles, or acknowledge D's renderer.
Import your authorized canonical CSV/JSON with the exact fields in `Event`; extra
fields (including private account data) are rejected. JSON must be an array; Decimal
values must be strings. CSV timestamps are integer UTC milliseconds. The fixture
function provides an executable example of the required manifest. Synthetic flag
is mandatory. Rights are exporter-attested, not independently certified by code.

## Boundaries

- Original bytes hashed; manifest and canonical events pin `dataset_revision`.
  Same source revision cannot change content. Source revocation blocks dependent
  plans, reads, retries and ACKs; revocation does not physically delete rows.
- Files <=8 MiB, rows <=100000, stored canonical payload <=16 MiB, pages <=500,
  analysis <=2000 selected events. JSON is bounded-buffer parsing, not streaming.
  This is a bounded snapshot store; million-row normalized B storage is not claimed.
- Latest visible revision replaces earlier versions before range filtering. Tied
  revision publication times are ambiguous and rejected. Source timestamps are kept
  exactly at supplied millisecond precision; no claimed nanosecond precision.
- Weighted-average linear unit model requires exporter-attested complete flat-start
  one-way history. Partial closes and reversals retain evidence. Hedge/inverse,
  anonymous prints, unknown sides, ambiguous simultaneous fills and clipped initial
  history return UNKNOWN. Model assumptions are explicit. Missing costs, SL/TP,
  net PnL, verified win rate and equity drawdown remain null. Completed-cycle gross
  statistics are INFERRED; no PnL aggregation across unknown instrument currencies.
- `entry_context` excludes points whose event or availability time is in the future.
  No trader intent or historical L2/L3 is inferred. `simulation_plan` pins proposed
  F DSL, market snapshot and F commit; exact approval is required before engine call.
- `Research` is a callable integration port, not an authenticated HTTP server or a
  replacement for E/G. Trusted caller supplies tenant and approval after capability
  enforcement. No user-provided SQL, shell or executable strategies accepted.
  D/G official render flow and E queue are pending peer implementation/acceptance.
- H uses `tester_trader`, leaving B migration versions untouched. B approval of
  production schema, normalized tables, retention/purge and export rights remains
  pending. A database owner can alter rows; application immutability is not a DB ACL.

## Tests

```powershell
python -m unittest discover -v
$env:TESTER_H_TEST_DSN=$env:TESTER_H_DSN  # ONLY isolated disposable acceptance DB
$env:TESTER_H_F_ROOT='PATH_TO_PINNED_F_CHECKOUT'
python -m unittest discover -s tests/trader_intelligence -v
```

F smoke uses its checked-in synthetic fixture and actual engine. It does not establish
an inferred real trader strategy. See `HANDOFF.md`, `SOURCE_AUDIT.md` and `TEST_REPORT.md`.
