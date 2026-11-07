# Canonical metadata handoff plan

Scope: Issue1 comment6709792128. Isolated existing A branch; no B/shared edits. Autonomous execution authorized in mission.

Design: add src/ingest/metadata.py for bounded instruments-info acquisition using existing API/Journal. Preserve original HTTP bytes, SHA256/hash scope, request scope, actual acquisition run/time and page terminal evidence. Output a self-contained JSON artifact with strict shared1.0 instruments and keyword arguments compatible with B e236395 Store.save_instrument. No new authoritative DB or automatic publication. Existing raw catalog remains unchanged; explicit catalog-handoff command opts in.

Alternatives: modifying the raw iterator risks existing callers and loses response evidence; direct B writes would cross ownership/default side-effect boundaries. A standalone versioned handoff reuses existing acquisition journal and keeps consumer review explicit.

- [x] Add failing tests: category/step validation, exact decimal strings, duplicates/conflicts, bounded/repeated cursors, raw evidence/time/run, stable replay/revisions and no COMPLETE.
- [x] Implement acquisition/projection and opt-in CLI; persist failed/capped attempts and raw responses without inventing fields.
- [x] Validate emitted instruments against immutable shared9766 schema and B save_instrument kwargs against pinned B source/fixture.
- [x] Run all A unit tests, focused review, record official semantics/commands/evidence and publish Draft10 head only.

Official semantics checked 2026-11-07: https://bybit-exchange.github.io/docs/v5/market/instrument . Spot has no pagination; limit/cursor invalid. Linear/inverse qtyStep and all categories tickSize are named increments. Spot basePrecision is coin precision, not documented qtyStep. Endpoint is a current queried snapshot; launch/delivery fields do not prove historical listing coverage. Missing mandatory instrument fields stay raw/unprojected.
