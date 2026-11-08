# J Strategy Lab admission evidence — 2026-11-08

Refs #32 and #8. F/G/H remain **NOT_INTEGRATED**. No installed application,
database, service, registry, credentials, real orders or main branch were changed.

## Evidence boundaries

`src/integration/lab_coverage_evidence.py` is a pure observed-grid receipt builder.
It binds supplied bytes by SHA-256, checks exact market/range, a contiguous closed
grid, OHLCV consistency, source scope and original metadata, and retains declared
source/page hashes and provenance. It has no API route or admission hook.
Its output always says `historical_completeness=UNPROVEN`,
`f_admission=NOT_READY`, and `historical_visibility_proven=false`.
The observation cutoff is not a historical availability timestamp.
Absent original HTTP bodies, declared source/page hashes cannot be independently
recomputed; hash equality to a supplied pin does not certify exchange origin.
An observed full grid cannot upgrade UNKNOWN coverage or empty evidence refs.

The saved public BTCUSDT linear one-minute snapshot for 2026-11-07 contains 1,440
closed rows and has SHA-256
`178e775375c39f360e858c2dc68b24a11af71f3e47888bfac0b8e853ea8826d6`.
Its grid checks pass, but declared availability remains PARTIAL and coverage
UNKNOWN. Exact published F metadata rejects the original row in both normal and
exploratory modes. No real-history strategy was run and no completeness proof was
manufactured. This is the current #8 → F admission result.

## Synthetic positive and source identity

`tests/integration/fixtures/f998/PIN.json` records Git blob identities and SHA-256
hashes at F source commit `998c5bc89d1b35c9ad3047b1e50dcb1295120e31` in
`YehorBabenko88/tester`. The two example JSON fixtures are exact published bytes.
The engine probe verifies every pinned Python producer file before importing the
opt-in source in an isolated subprocess. Nothing is copied into application
packages. Fixture COMPLETE/fixture:bars is explicitly synthetic and cannot prove
real-market completeness.

The opt-in probe verifies admission warnings SYNTHETIC_DATA/NON_CERTIFIED,
deterministic replay and checkpoint restore, a future-bar mutation leaving the
processed prefix and signals unchanged, SL before TP on an ambiguous bar,
hand-calculated closed-trade net PnL -5.195 and fees 0.195, and a separate TP case.
It saves/reloads an immutable portable JSON artifact in pytest temporary space,
checks idempotence, and rejects changed inputs and tampered persisted bytes.
This verifies F's portable artifact implementation, not B/E PostgreSQL persistence.

Every synthetic fill has exactly one F overlay with matching fill/order identity,
price, quantity, role, reason, timestamp, known_at, time window and precision.
An open position has entry markers and no closed trade: no trade ID is invented.
An intrabar exit remains bar-uncertain and is only known at close. G's pinned
six-key marker contract cannot preserve these fields and requires trade_id;
therefore no lossy adapter, endpoint or D renderer is enabled. D/E wiring and
canonical persistence remain NOT_INTEGRATED, as specified in the preparation doc.

## Verification and reproduction

With the pinned published F checkout and saved snapshot paths supplied through
`TESTER_F_PUBLISHED_ROOT` and `TESTER_OBSERVED_SNAPSHOT`:

```text
python -m pytest tests/integration -q
python -m ruff check --no-cache --isolated --select E,F,I,UP --ignore E501 src/integration/lab_coverage_evidence.py tests/integration/test_lab_coverage_evidence.py tests/integration/f_published_probe.py
```

Result: 53 passed, 2 skipped. The skips are existing disposable PostgreSQL tests
without TESTER_TEST_ADMIN_DSN; no database was contacted. Ruff passed. The focused
new suite has 24 passing cases with both inputs. Without optional inputs, its
20 fixture tests run and four producer/saved-snapshot checks skip explicitly;
no substitute F validator or real market input is fabricated.

Adverse tests cover missing/duplicate/reordered bars, unclosed bars, wrong market,
inconsistent OHLCV, fabricated COMPLETE metadata, damaged source hash/time/run,
wrong input hash, cutoff beyond capture, insufficient observation cutoff, boolean
times and unsupported/wrong interval scope. Remaining product acceptance gates
are authentic B completeness/visibility evidence, reviewed versioned F/G/D/E
projection, B/E canonical persistence, authorized durable jobs and actual UI wiring.
