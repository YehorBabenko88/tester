# H contracts and peer acceptance

H continues PR26 from 3837f1a2b95343f6ad6d24a88ba89a7a438e78f3.
Original tests: `python -m unittest discover -s tests/trader_intelligence -v` 10 PASS.

- B (#2/PR13): reserve `tester_trader` schema, H-owned checksummed DDL; source consent,
  immutable snapshot JSON and analysis requests committed transactionally. Please
  review retention/revocation semantics and incorporate into storage inventory.
- C (#3/PR20): gross model PnL only for flat-start complete linear one-way histories;
  missing fees/funding/net/drawdown unknown. Context only with available_at <= decision.
- F (#16/PR19): candidate rules remain INFERRED, require approval and frozen DSL,
  market snapshot and engine revision; no automatic strategy extraction or orders.
- E (#5/PR9): bind H callable service to authenticated bounded trader endpoints;
  tenant from trusted authentication, never request JSON. Reuse durable E job lifecycle.
- D (#4/PR12): markers pin source/data/run revisions, distinguish DISCLOSED/INFERRED,
  display lag and unknowns; ACK digest + renderer revision. Never mark coverage verified.
- G (#18/PR24): selected event -> read-only plan digest -> explicitly approved run ->
  artifact -> render ACK. Add trader capability to your allowlist; current G rejects it.

Publication is a coordination request, not peer approval or verified integration.
