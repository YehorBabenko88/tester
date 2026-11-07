# H execution plan — 2026-11-07

Continue PR26 and issue25; no main merge, deployment, service installation or orders.
Use existing Python core, Decimal strings and B-style transactional PostgreSQL with
a separate `tester_trader` schema (no changes to B's migration sequence).

1. Execute original ten tests (PASS at 3837f1a).
2. Audit official Bybit/Binance docs; admit only explicitly authorized local exports.
   UI visibility alone does not grant automated download/redistribution rights.
3. Test first: bounded CSV/JSON imports, duplicate/revision conflicts, as-of and
   cross-instrument identities; immutable source/data revisions and revocation.
4. Reconstruct only complete, explicitly flat-start, linear one-way executions.
   Partial closes use weighted average cost; hedge/inverse/ties/unknown inventory
   return UNKNOWN. Gross model PnL is INFERRED; fees/funding/net/SL/TP unknown.
5. Add PostgreSQL transactional import, immutable runs, request conflict protection,
   restart-safe retry, bounded pagination, artifact digest and render ACK.
6. Provide callable research API (E authenticates), selected-event plan and approved
   analysis (G authorizes), truthful overlays (D renders); C/F receive context and
   hypothesis contracts. No direct SQL/shell or generated executable strategies.
7. Execute real isolated PostgreSQL integration: rollback/restart, concurrent retries,
   ten instruments, conflict/revocation, memory bounds; save exact evidence.
8. Independent review, fix findings, publish exact HEAD/tests/limitations in Draft PR.

Alternatives: scraping is unadmitted; an in-memory-only engine cannot test restart;
merging peer branches into H would obscure ownership. H implements a callable
research scenario and explicit peer ports, with cross-workstream UI/HTTP acceptance
reported separately until peers acknowledge and execute the contracts.
