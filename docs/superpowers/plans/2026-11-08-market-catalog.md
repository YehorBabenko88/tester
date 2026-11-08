# Issue 40: independent catalog foundation

Approved scope comes from Issue #40 and the coordinator assignment. This slice deliberately does not mount routes in the installed/native app or change its configuration. A subsequent composition must explicitly attach the router and own its scheduler lifecycle.

Use one category-keyed in-process service instead of browser-owned refresh requests. Acquire every cursor page before atomic publication; expose failed attempt progress separately from the retained last good snapshot. Use a bounded page budget, reject cursor cycles and malformed/duplicate records, and never call a partial universe complete. Snapshot publication is immutable to callers. A five-minute single poller owns refresh, failure backoff and shutdown cancellation; requests only query snapshots or signal refresh.

Metric values carry endpoint, observation time and state. Ticker 24h values retain their native units; unavailable volatility, market cap and unobserved one-hour/OI deltas stay null with reasons. No synthetic history is inferred. Global sort and filter happen before bounded pagination, nulls always last and ties resolved by category/symbol. Revision conflicts prevent mixing pages from separate refreshes.

1. Add failing tests for >1000 instruments, continuation/cycles/failures, atomic retention, category separation and full-universe sorting with nulls.
2. Implement `src/api/market_catalog.py`: acquisition, snapshot service, metrics/query and opt-in FastAPI router. No database, downloads or existing canonical contract mutation.
3. Add scheduler tests and implementation for one task per service, five-minute cadence, nonoverlap, manual coalescing and bounded failure backoff.
4. Run targeted pytest, Ruff and formatting. Publish a separate Draft PR against the integration branch with exact source SHA and explicit remaining Issue40 gates.

Alternatives: replacing the existing instrument provider would change pinned canonical contracts; fetching per browser would multiply load and race snapshots. The additive explicit router is the smallest independently testable boundary. Persistent PostgreSQL observation storage and the unified UI are separate follow-up work and this slice must not claim final Issue40 acceptance.
