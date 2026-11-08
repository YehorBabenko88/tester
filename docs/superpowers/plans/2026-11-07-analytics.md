# Analytics v1 Implementation Plan

**Goal:** Deterministic, provenance-preserving canonical analytics; no merge.
**Architecture:** Pure Python dict records, shared v1 schema snapshots for conformance, isolated analytics extensions for metrics the strict v1 schema cannot represent.
**Spec:** Issue #8 and Issue #3 decision 6707785623; schema pinned to coordinator 9766c0093e2608280875ac56db074414a3d62c69.
**Tech Stack:** Python 3.11+, Decimal, Fraction, unittest, jsonschema for conformance tests only.

## Constraints and review focus
Only src/analytics, analytics-specific tests and documentation change. No invented missing trades; source conflicts fail explicitly; completeness cannot be inferred from candle closure, source exhaustion or empty input. Genuine source precision preserved. Test unknown sides, same-ID changed payload, huge decimal sums, gaps and calendar transitions.

## Tasks
1. Write synthetic duplicate and huge-decimal regression tests against initial aggregate; run and preserve failing output.
2. Write tests for canonical normalization, scoped dedup/conflicts, sub-ms ordering, UTC intervals and inheritance; run red. Implement core normalization and aggregation, run green.
3. Write canonical schema, unknown-side delta/CVD, price-level, VWAP/profile/POC/value-area and custom interval tests; run red, implement isolated extensions, run green.
4. Write comparison coverage/boundary/tolerance and supplemental analytics tests; run red, implement, run green.
5. Run full unittest discovery and shared schema conformance, inspect diff, document formulae and limitations. Publish atomic commit on workstream/c-analytics, Draft PR and exact evidence report in #3/#6. Never merge.
