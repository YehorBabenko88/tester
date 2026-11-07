# Agent F test report — 2026-11-07

Repository: YehorBabenko88/tester. Branch: workstream/f-strategy.
Starting SHA: 13ffae2cbedea096c9bc81b03933bf3a6488f438.
Environment: Windows, Python 3.14.0; engine uses Python >=3.12 standard-library APIs.
Independent code reviewer examined the implementation and ran the original 24 tests.

## Observed verification

- `python -m unittest discover -s tests/strategy -p "test_*.py" -v`: **39 passed**.
- `python -m unittest discover -p "test_*.py" -v`: **9 passed** (existing baseline).
- `python -m compileall -q src tests/strategy`: exit 0.
- JSON Schema Draft202012 schema check: accepted; example accepted; unsupported
  schema version and arbitrary code property rejected (jsonschema 4.26.0 verifier).
- `git diff --check`: clean before publication.

No network market-data acquisition, database mutation, real trading operation, live
order endpoint, deployment or UI interaction occurred. Examples use synthetic
fixtures and explicitly carry NON_CERTIFIED/SYNTHETIC_DATA warnings. Installing
the repository's declared psycopg dependency resolved the initial baseline import
failure; no source change was required. No tests were skipped in the two suites.

## Acceptance evidence

| Requirement | Evidence |
|---|---|
| Deterministic runs | Complete result equality; repeated seeded grids |
| No lookahead | Future-bar mutation leaves prefix signals/digest unchanged; late feature skipped; pivot invisible until right-bar close |
| SL/TP | Hand-calculated 100 entry / 95 SL / 110 TP; ambiguous range resolves to SL; open gap target before later low |
| Fees | 0.001 rate: 100 entry fee 0.1 + 95 exit fee 0.095, net -5.195 |
| Incomplete history | Reject PARTIAL/UNVERIFIED; explicit exploratory gap warnings |
| Data gaps | Missing interval rejected; stale pending entry cancelled; adverse open80 fills SL at80 |
| Decimal | 1e-18 fee retains net -5.000000000000000195; ambient context6 does not change engine or risk validation |
| Concurrent strategies | Three ThreadPoolExecutor runs equal independent serial result |
| Stop/resume | JSON checkpoint restore/replay equals full run; tampered checkpoint rejected |
| Chart correspondence | Fill IDs bijective with overlay markers; prices/times originate from actual simulated fills |
| Funding | Held Long cost1 on notional100/rate0.01; no payment/receipt for new position after settlement; missing schedule rejected |
| Partial fills | Liquidity evidence limits entry to0.5; residual entry cancels; three exit legs reuse one residual order |
| Margin | Oversized request rejected; leveraged maintenance liquidation fixture; full-request reservation policy pinned |
| Levels | Delayed pivot, ties, min touches, close/wick break, retest, false break, calendar leap clamp, expiry |
| Research | 4-candidate reproducible bounded grid; disjoint train/validation/test; test mutation cannot select winner; nonoverlapping walk-forward tests |
| Artifacts | Full inputs/results saved idempotently; content change fails SHA integrity |
| DSL/text | Unsupported operators/keys/floats rejected; confirmed preview digest pinned; unsupported prose remains unresolved |

## Review corrections

1. Important: full OHLC protective check incorrectly preceded an eligible open exit.
   A failing numerical regression reproduced this; separate open/range phases fix it.
2. Important: repeated partial protective exits abandoned an active residual order.
   A failing regression reproduced this; fills now reuse that order through completion.
3. Additional boundary checks pin open-gap target chronology and local Decimal
   precision during standalone strategy validation.

Ruling: full-request margin is reserved before partial participation. This prevents
oversized requests from relying on unknown eventual liquidity; it can reject a
request whose smaller actual partial quantity would have been affordable. It is
intentional, documented in FORMULAS.md and covered by a regression test.

## MASTER corrective follow-up — engine 1.1.0

PR19 comment 6708269302 identified two further P1 behaviors that the original31
green tests did not cover. Both were reproduced with explicit synthetic JSON DSL:
entry consumes current capacity and stop disappears after rebound; whole-bar future
capacity controls a fill falsely labeled modeled_open. The reviewed baseline was
c72cf50504b23935b256c7bf5d904f963a126c95; root MASTER independently confirmed31/31
baseline passes (the reviewer's os.link sandbox error did not reproduce full-access).

Corrections: latch protective market exits before the zero-size check; preserve
trigger/order/reason through rebound, gaps and replay. Require matching liquidity
range, close-time availability, complete coverage, provenance and evidence refs;
reject late/premature/partial/unavailable or legacy evidence. All whole-bar-capacity
fills and overlays now have bar uncertainty and known_at_ms at close, never an exact
opening liquidity claim. Conservative no-same-bar-target applies to these entries.
Engine version is bumped to strategy-bar-1.1.0; MIGRATIONS.md details compatibility.

Eight added test methods cover consumed entry capacity, zero-capacity TP/liquidation,
gap/checkpoint persistence, future-capacity mutation prefix, uncertain fill/overlay
knowledge, legacy rejection and eight invalid-evidence subcases. All39 strategy
tests pass; an independent focused reviewer also ran39/39 with no new findings.
Evidence and source-tree digest: evidence/MASTER_FOLLOWUP_TESTS.log.

## Integration gates / declared model limits

- MASTER must run the explicit strategy-suite command in integration CI: existing
  root discovery does not enter namespace-only tests/ on Python3.12. CI itself was
  not edited outside F ownership. Local Python3.12 execution is not claimed.
- E must apply the approved shared v1 validator to canonical rows, persist actual
  user preview approval and expose job endpoints. D must render uncertainty windows;
  the tested fill/marker correspondence is a data contract, not UI end-to-end proof.
- B owns canonical PostgreSQL persistence. F ships portable immutable JSON artifacts.
  C must supply historically available features with truthful evidence/version timing.
- General prose translation uses a reviewed E-side candidate preview; built-in text
  grammar is narrow. Unsupported ambiguity never silently becomes a runnable rule.
- No real-data conformance or market profitability claim. Fixed configured spread;
  no observed quote/queue/tick reconstruction. Partial capacity requires explicit
  finer-evidence references. Simplified liquidation lacks exchange tiers/ADL/fees.
- Only spot Long and linear Long/Short; inverse payoff, borrowed spot, calendar-month
  candles and intrabar funding settlement without boundary evidence are rejected.
- Metrics count realized exit legs; exposures are observed-bar upper bounds. Open
  positions remain marked, not silently force-closed. Statistical confidence is
  reported unavailable rather than making unsupported IID assumptions.

Historical performance does not guarantee future returns. Draft PR only; no merge.
