# Strategy Lab implementation plan

Goal: offline, reproducible historical simulation conforming to CONTRACT.md and Issue #8.
Architecture: Python standard library only; isolated DSL, point-in-time evaluation,
level detector, event simulation, metrics/artifacts and chronological optimization.
No exchange client, HTTP endpoint, UI or database changes.

1. Write failing DSL tests; implement strict versioned validation, safe expression
   compilation and text preview confirmation. Reject unknown keys and non-string money.
2. Write analytical tests; implement closed-bar expression evaluation, availability
   cutoffs and delayed pivots, calendar-month windows and level lifecycle events.
3. Write hand-calculated event fixtures; implement quality gates, next-evidence fills,
   fees/funding, risk, gaps, liquidity evidence, checkpoint replay and overlays.
4. Write optimization tests; implement bounded grid, disjoint chronological splits,
   walk-forward selection and held-out scorecards with stability disclosure.
5. Run full baseline and strategy suites; review boundary behavior; document formulas,
   limitations, integration formats and evidence; publish Draft PR and update #16.

Review focus: delayed/revised analytics; duplicate/nonclosed candles; gap-held positions;
same-candle entry and bracket ambiguity; partial fills and capital conservation.
Each is pinned by numerical or integration fixtures before implementation.

Execution: directly in isolated fresh clone on user-specified workstream/f-strategy.
The user explicitly requested immediate autonomous implementation, without design gates.
