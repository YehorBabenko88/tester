# Contract version migration rules

Approved source: Issue #8. Current concrete version: `1.0`. All normalized records and API envelopes require schema_version. Unknown/incompatible versions are rejected; there is no implicit float/time conversion.

The earlier unpublished coordinator proposal `1.0.0` was not deployed and is superseded by Issue #8. No persisted user data is rewritten by the contract package. B owns PostgreSQL schema/versioned database migrations; fresh legacy db.initialize checks are not proof of B migration correctness.

For pre-v1 adapters, explicitly map venue/category/symbol and original trade_id; `exchange_timestamp_ms` to canonical timestamp_ms only when the source unit is known. Preserve source-provided sub-ms precision, raw source timestamp and ingestion_run. Coarse sources produce null timestamp_ns. Decimal values must originate in exact strings/Decimal; a float cannot be repaired by formatting it.

Map old lowercase availability to AVAILABLE/PARTIAL/UNAVAILABLE/UNVERIFIED with a reason. Do not convert completeness from is_closed, cursor exhaustion, empty pages or download percentages. Existing cached complete flags lacking source proof must be revalidated, never blindly migrated to COMPLETE.

Old source_refs/provenance must become explicit source kind/ref/hash/run/precision entries. Derived records add parent references and method version. Missing evidence stays UNVERIFIED/UNKNOWN, never invented. Official-versus-derived comparison contexts (coverage, boundary, rounding, tolerance) must remain alongside comparison results.

Run shared `validate_record`/`validate_response` and producer/consumer fixture tests before enabling each adapter. API data payloads require row validation; generic envelope JSON Schema alone is insufficient. Future breaking field/meaning changes require a new version directory, migration fixtures and cross-agent review on #8.

