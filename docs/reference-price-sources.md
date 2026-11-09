# Explicit bounded mark/index source profile — Issue58

Base installed source6a24e50d51f4d65783c4436b94fe87cc883acbeb. Code-only draft;
no source request, PostgreSQL, process/runtime, browser or installation performed
by contributing agent. Actual probes/physical acceptance belong to ROOT only.

## Fresh owned-root invocation (ROOT only, after review)

Use the existing reviewed research_lab preflight/create procedure to prepare a NEW
owned root/PG cluster. ROOT must archive and verify this newly published candidate
and its immutable manifest, then run from its exact SHA-named source directory:

```
python -B -m src.integration.pc4_runtime prepare-price-sources --root NEW_OWNED_ROOT --lab-source-sha LAB_SHA --application-source-sha NEW_CANDIDATE_SHA
python -B -m src.integration.pc4_runtime limit-role --root NEW_OWNED_ROOT --lab-source-sha LAB_SHA --application-source-sha NEW_CANDIDATE_SHA
python -B -m src.integration.pc4_runtime serve --root NEW_OWNED_ROOT --lab-source-sha LAB_SHA --application-source-sha NEW_CANDIDATE_SHA
python -B -m src.integration.pc4_runtime verify --root NEW_OWNED_ROOT --lab-source-sha LAB_SHA --application-source-sha NEW_CANDIDATE_SHA
python -B -m src.integration.pc4_runtime shutdown --root NEW_OWNED_ROOT --lab-source-sha LAB_SHA --application-source-sha NEW_CANDIDATE_SHA
```

No old-root config edits/adoption. The published prepare action selects the static
`reference-prices/1.1` profile before market seed; its receipt stores that profile
and exact config hash. Runtime reconstructs only the two allowlisted profiles and
retains exact source/DSN/config/ownership guards. Normal prepare/seed stays candles.
Standalone market_runtime also exposes seed-price-sources for an operator-owned
market-only lab; ordinary seed remains unchanged. Limited-role handoff must still
precede runtime activation; no privilege guard or grant was broadened here.

## Capabilities and versions

Explicit config factory pair:
`src.integration.reference_prices:create_reader` and `:create_handler`.
Typed DownloadRequest admits raw kinds mark/index additively; only the explicit
reference handler supports them. Original LabHandler/CatalogHandler still reject
these jobs. Funding/open_interest/trades remain NOT_INTEGRATED in this profile.
Source listing advertises only candles/mark/index and the explicit price-only port.

Existing `/api/candles` v1.0, default factories and native/product startup are
unchanged. ONLY the reference reader profile mounts typed `/api/price-candles`:

```
GET /api/price-candles?schema_version=1.1&category=linear&symbol=BTCUSDT&start_ms=START&end_ms=END&interval=1&candle_kind=official_mark&limit=100
```

Use official_index for index; inverse is also typed where actual source instrument
supports it. interval is ORIGINAL source cadence, never a viewport aggregation.
The endpoint/envelope and records are explicit1.1; shared price-only validator
checks OHLC, UTC/fixed/calendar boundaries, provenance and absence/null-only volume.
No fabricated volume/turnover or v1.0 migration. Per-app HMAC cursors bind version,
raw kind, category/symbol/range/cadence and page parameters; restart invalidates
tokens. Reader uses one bounded B read_page (1000 records max, archive reads0),
checks authentic original caller runs and stored projections, blocks corrupt or
unavailable history. Response is always UNKNOWN coverage and observed PARTIAL
availability, or empty UNAVAILABLE; it cannot certify historical visibility.

## Source publication

A already supports mark-price-kline/index-price-kline and preserves original HTTP
body bytes+SHA256 in acquisition manifests, separate canonical-row source hash,
original timestamp_ms/source timestamp string, exchange closure time and fetched
UTC. B already supports mark/index OHLC without volume. A's legacy shared-v1.0
projection correctly rejects the missing volume; this new adapter explicitly
adopts the existing shared price-only1.1 contract without editing A or v1.0.

Only closed exchange-time-qualified bars are projected. Endpoint host/path,
category/symbol/interval, original ms precision and source timestamp are checked
separately from candle_kind; funding/other endpoints cannot be relabeled mark.
Unverified/quarantined acquisition cannot be promoted to observed availability.
Original Decimal strings/provenance/HTTP receipts are retained; B normalized values
are reread and checked semantically against the source projection in the same
existing E fenced publication transaction. B ingest is required to remain
unverified; projection insertion and measured acknowledgment share that fence.
Existing retry, dedup, conflict findings, metadata, disk and TLS paths are reused.

D price-panel wiring, /api/analytics, delta/CVD/footprint and other analytics remain
NOT_INTEGRATED. This port does not calculate analytics or admit raw reference data
to F as real verified history. Funding/OI/anonymous executions are not enabled.

## Executed evidence

TDD RED: new module missing; separate explicit selector missing; actual dict_row
projection read regression failed before fix. GREEN commands from own worktree:
```
../../venv/Scripts/python.exe -m pytest -q tests/integration/test_reference_prices.py tests/integration/test_catalog_market.py tests/integration/test_lab_market.py tests/platform/test_market_runtime.py tests/platform/test_pc4_runtime.py tests/platform/test_runtime_identity.py tests/platform/test_api.py tests/platform/test_canonical.py tests/platform/test_request_scope.py --basetemp=../../mark-index-final
```
145 passed,1 dependency deprecation warning,1.74s. Ruff check/format pass.
New tests replay genuine A Backfill+Journal with in-memory source responses, retain
original HTTP SHA/body/UTC and B UNVERIFIED batches, verify both raw kinds/no volume,
closure/source identity/exact Decimal preservation, reader authenticated provenance,
price1.1 route/default isolation, typed OpenAPI and explicit profile selector.
PostgreSQL/real HTTP source/browser/process effects were not executed or certified.
ROOT still must review, publish immutable candidate and physically test one bounded
sample, then scoped larger samples, under the limited runtime role.

Independent review P2 follow-up: route plain-int parameters accepted decimal-shaped
time strings such as0.0. Regression RED observed; start_ms/end_ms now reuse the
existing strict HttpTime parser, matching the v1.0 time boundary. Full145-test
focused suite PASS again; Ruff PASS. No source/network or PG action.
