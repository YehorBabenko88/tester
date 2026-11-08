# Runnable pinned F synthetic research adapter

This integration-wave source implementation supersedes the earlier fixture-only
J admission preparation for offline synthetic execution. It does not claim a
deployed product, canonical PostgreSQL wiring, physical acceptance, real market
coverage or actual trader execution. Coordination refs: #32, #8 and PR #30.

## Actual producer and callable interface

`src/backtest` and `src/strategy` are exact same-repository F source at
`998c5bc89d1b35c9ad3047b1e50dcb1295120e31`, freshly confirmed against its remote
branch before vendoring. `src/integration/F_SOURCE_PIN.json` records Git blobs and
SHA-256 for ten producer modules, six producer test files and two packaged
published fixture JSON files. Git attributes preserve their exact bytes.
No financial logic was rewritten. The wheel includes the producer, fixture and
pin data; no external worktree, local preflight directory or fake backend is
needed for execution.

```python
from src.integration.strategy_lab import (
    run_certified_fixture,
    run_synthetic_research,
    verify_research_bundle,
    serialize_research_bundle,
    project_research_chart,
)

bundle = run_certified_fixture()  # Executes actual F on packaged source fixtures.
verify_research_bundle(bundle)   # Genuine replay plus exact canonical-byte check.
canonical_bytes = serialize_research_bundle(bundle)
early_chart = project_research_chart(bundle, cutoff_ms=60000)
```

`run_synthetic_research(strategy, snapshot)` accepts bounded JSON dictionaries,
at most 2,000 candles and 16 MiB per input/output object. It never repairs quality
labels or turns real observations into proof. All nested provenance must say
synthetic_fixture with fixture:/synthetic: references; availability must be
AVAILABLE and coverage COMPLETE with explicit synthetic refs. Authentic sources,
UNKNOWN coverage and missing refs fail. F's actual grid validation must report no
gaps even if an exploratory strategy would permit them. F retains SYNTHETIC_DATA
and NON_CERTIFIED warnings. Certification means only SYNTHETIC_ONLY;
`real_market_coverage=NOT_PROVEN` is invariant.

## Immutable bundle and projection

Bundle schema `tester.strategy-research-bundle/1` has these exact top-level keys:

```text
schema_version, source_sha, engine_version, synthetic,
strategy, snapshot, strategy_digest, snapshot_digest,
result, result_digest, chart, chart_digest, certification
```

Inputs and the full genuine F result are retained. Digests are SHA-256 of sorted,
compact, finite canonical JSON. Verification reruns actual F and compares full
canonical bytes, rejecting altered fields even if a caller recomputes a digest.
It distinguishes booleans/integers and integers/floats. There is no persistence
authority in this adapter: K/root must store canonical bytes and digests under
reviewed transactional authorization.

The agreed K/L `tester.research-chart/2` projection includes run_id (actual F run
hash), snapshot_id (`synthetic:` plus actual F market snapshot hash), revision
(exact F source SHA), venue/category/symbol/interval, half-open start/end, cutoff,
SIMULATED, source_ids, synthetic, coverage, candles and markers. Full original
provenance remains in the bundle. Source IDs bind that original synthetic
provenance by hash; they do not independently certify exchange origin.

Candles retain integer start/end and exact open/high/low/close strings. Only
closed candles wholly in view and known by cutoff are shown. Every visible fill
has exactly one marker: fill/order IDs, position_id, nullable closed_trade_id,
entry/exit role, execution buy/sell side, timestamp, known_at, window start/end,
precision and exact price. Full quantity/fees/reason and original long/short
direction remain in `bundle.result.fills` and must be associated by fill_id.

F has no standalone position ID. The adapter explicitly derives the namespace
`position:<actual_entry_fill_id>` from the actual entry relationship. Entry
closed_trade_id is null; every exit uses its genuine F trade ID. Multiple partial
exits share the actual entry-derived position and order while retaining distinct
closed trade IDs. No closed identity is invented for an open entry. All orders,
fills, trades, open positions and F overlays are checked for consistent identity
and complete fill correspondence before projection.

The chart admits at most 500 visible markers; excess fails rather than truncating
or pretending pagination. Prices must fit the agreed exact decimal contract or
fail, never round. A custom view stays within the immutable snapshot. Fills known
after cutoff are omitted; underlying raw evidence remains complete in the bundle.
An early cutoff chart reports partial coverage. No endpoint or arbitrary
provider expression is accepted by this module.

## Evidence and checks

Actual published fixture output:

- Genuine F engine `strategy-bar-1.1.0`: three fills, three chart markers, three
  closed candles; first trade stop_loss, net PnL -5.195 including fees 0.195.
- Canonical bundle SHA-256:
  `aa7fd354761774a4c3d2a74da6e077c4b4d4f06fe45c86d8658bcbe73e16e6ce`.
- Chart digest:
  `b2704ca080a0987622b062fa088372894537372a3b8cd15705f518edcd4b6d44`.
- Both full and early-cutoff actual charts validate unchanged against K model
  source `d38daa73ac899bcec61d4b540d115ffe365c8384` loaded read-only from Git.
- All 12 packaged producer/fixture byte pins match in an extracted built wheel;
  an isolated `-I -B` process imported only that wheel directory, ran genuine F
  and verified the same bundle/chart hashes. The shared development environment
  was not modified; wheel build used isolated build dependencies.

The 21 adapter cases plus all 39 original producer tests pass (including eight
producer subtests). Checks cover deterministic SL/TP/fees and replay, original
producer no-lookahead/checkpoint tests, synthetic/authentic/UNKNOWN admission,
caller-rehash attacks, canonical JSON type attacks, source pin integrity, cutoff
visibility, actual short execution sides and three partial exits with exact IDs.
Explicit Ruff checks pass for adapter/tests. The combined recovery, integration
and producer run is recorded in the PR validation report.

No installed files, services, PostgreSQL 5432, UAC, native recovery, HTTP API,
credentials or real orders were accessed. Actual durable-store/authenticated
backend/browser evidence is owned by K/L/root and must be reported separately.
Existing saved public history remains UNKNOWN / F NOT_READY; it was not run.
