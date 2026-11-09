# Issue59 qualified PostgreSQL snapshot seam

Base ea4d9b2cbfc15e1af54ea6bf73a530b68763873e. Owner B/A/E data-snapshot,
branch workstream/pc4-qualified-snapshot-20261109. Tester only. No runtime startup,
install, office/market network or PostgreSQL action was executed locally.

## Interface agreed with F/J execution owner

`src.integration.qualified_snapshot.SnapshotRequest` is a frozen exact market/range
and optional expected dataset revision. It admits only supported fixed F intervals,
spot/linear, at most1000 bars and safe Bybit identity; no calendar M/inverse payoff.
`read_canonical_snapshot(BStore, request)` calls one bounded B read_page, retaining
all returned raw rows, Decimal strings, ms/ns precision, source observations,
ingestion runs, range proofs, knowledge facts and database read receipt. It never
stitches transactions or invents event/knowledge timestamps. max_archive_bytes=0
prevents this read seam from acquiring archive files; unsupported archive proof is
explicitly unavailable. Dataset revision hashes source facts, not a new DB read
clock. Wrong expected revision fails closed.

`QualifiedSnapshot` frozen DTO exposes schema_version tester.qualified-snapshot/1,
snapshot_id, mode SYNTHETIC|REAL, snapshot_digest, dataset_revision, evidence_digest,
admitted, reasons, certification, snapshot and provenance. `.snapshot`/`.provenance`
return copies; `.as_dict()` serializes the exact wrapper. JSON byte limit16MiB.
REAL is UNVERIFIED/admitted=false with no F snapshot. Current B knowledge_time is
explicitly UNAVAILABLE: transaction starts do not prove historical visibility.
Even a supplied COMPLETE or positive knowledge label cannot upgrade this gate.
No financial calculations or producer source files were changed.

`certified_fixture_snapshot()` verifies existing F998 exact source+strategy/snapshot
fixture hashes and existing F quality validator, returning explicit SYNTHETIC_ONLY
admission. It does not execute the engine. `verify_qualified_snapshot` requires exact
byte-pinned synthetic inputs and wrapper; caller-rehash/relabel cannot certify new
data. Synthetic provenance always says real_market_coverage=NOT_PROVEN. This is
fixture certification, never verification of Bybit historical data.

`src.integration.snapshot_repository.QualifiedSnapshotRepository(dsn)` inherits
the existing explicit dedicated loopback non5432 tester_research_* DSN and numeric
hostaddr-pinning policy. Constructor has no connection/DDL. Explicit `.migrate()`
creates only tester_snapshots.qualified. `.publish_fixture()` publishes only exact
packaged synthetic input, INSERT ON CONFLICT DO NOTHING plus same-transaction
readback verification (no UPDATE or arbitrary JSON publish). `.read(snapshot_id)`
uses one REPEATABLE READ READ ONLY transaction, bounds encoded document transfer
in SQL and revalidates full fixture/identity before returning the DTO. Unknown,
oversized, changed, wrong-version or caller-rehashed documents fail. Stable IDs and
immutable publication support idempotent restart readback. Application operators
must register from this server repository, never from HTTP snapshot JSON; input
digest/actor grants remain F/J/G owner's separate authority.

## Executed evidence

Tests written first: missing qualified_snapshot module caused collection RED;
missing snapshot_repository module independently caused RED. Then pure tests:
```
../../venv/Scripts/python.exe -m pytest -q tests/platform/test_qualified_snapshot.py tests/platform/test_snapshot_repository.py -k "not postgres" --basetemp=../../pc4-snapshot-formatted
```
23 passed,1 real-PG test deselected. This includes real COMPLETE/UNKNOWN/empty/
resource exhaustion failclosed, revision stability/skew, Decimal preservation,
fixture namespaces/pins/caller-rehash, immutable projections, read-only SQL,
bounded transfer, publication conflict/no overwrite, missing/forged docs and DSNs.
Specified existing Ruff executable check and format--check pass after formatting.

`.github/workflows/qualified-snapshot-postgres.yml` adds exact-head hosted CI with
disposable PostgreSQL16/non5432, migration twice, fixture publish twice, restart
repository readback, and genuine F run from the PostgreSQL-returned snapshot.
No mock route/source data is used by that PG case. Hosted CI has NOT yet been
observed running/passing at publication. No local PG acceptance claim. F/J owner
adds durable pair-granted execution and D/G chart ACK integration independently;
this PR alone does not supply API/UI controls, real historical admission or the
full Issue59 acceptance scenario. No startup migrations/default enablement.
