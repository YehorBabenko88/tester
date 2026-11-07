# Agent coordination protocol — approved contract v1

Repository: **YehorBabenko88/tester only**. Master tracking #6; normative contract [Issue #8](https://github.com/YehorBabenko88/tester/issues/8). The user-approved Issue #8 supersedes earlier proposals. Coordinator branch `coordination/master`; candidate `integration/master`. MASTER does not modify workstream branches or merge main automatically.

## Mandatory reports

Publish in your own Issue/PR after each milestone or blocker:

```text
AGENT_ID:
ISSUE:
BRANCH:
HEAD_SHA:
PR_URL:
STATUS:
COMPLETED:
IN_PROGRESS:
BLOCKERS:
INTERFACE_CHANGES:
TESTS:
CI_RUNS:
EVIDENCE:
NEXT_ACTION:
```

STATUS: NEW, IN_PROGRESS, BLOCKED, READY_FOR_REVIEW, CHANGES_REQUESTED, ACCEPTED. Legacy NOT_STARTED maps to NEW. Execution is a separate field: RUNNING_CONFIRMED, AWAITING_AGENT_EXECUTION or NEEDS_USER_INPUT; a task comment does not prove a session ran. Include full immutable HEAD SHA. Tests need exact command, interpreter/platform, exit code, counts, log and fixture/live classification. CI needs run URL matching that SHA. Comments alone do not prove success. Each acceptance decision applies only to its reviewed SHA.

## Approved contract and concrete encoding

Pipeline: **RAW → VERIFIED → NORMALIZED → AGGREGATED → VISUALIZED**. Every derived record inherits provenance, availability and separate coverage. Normalization and closed time boundaries never imply historical completeness.

`contracts/v1/market.schema.json` fixes concrete JSON encodings; `contracts/v1/validate.py` provides structural and semantic validation, without ingesting, storing or calculating analytics. `schema_version="1.0"` is mandatory on records/API envelopes. E validates endpoint payloads as well as envelopes; the generic `data` field alone does not validate an endpoint's records.

- Identity: `(venue,category,symbol,trade_id)`; venue is bybit, category spot/linear/inverse, original trade_id preserved. Synthetic composite fallback has `id_origin="synthetic"` and documented `identity_method`; B must collision-test it. A fallback key cannot independently prove complete coverage.
- `timestamp_ms` is required integer UTC epoch milliseconds, bounded to exact JSON integers. All ranges are half-open `[start_ms,end_ms)`. `timestamp_ns` is a decimal integer **string** or null, preserving source precision through JavaScript. Source `timestamp_precision` is s/ms/us/ns and raw `source_timestamp` is retained. Coarse s/ms sources MUST have null ns; source ns is preserved exactly. Source us may be represented as ns only with trailing 000 and explicit us precision; this is unit conversion, not an assertion of ns measurement precision. Source and normalized millisecond values must agree. B's earlier ns-only wording must explicitly map microsecond sources rather than discard sub-ms information.
- Prices/quantities/volumes/rates are exact decimal JSON strings, no float round-trip, exponent, NaN/Inf or terminal newline. Execution price/quantity and instrument steps are positive. OI is nonnegative; ratio is [0,1]; funding is signed. Units remain dataset/market-specific.
- Provenance: source kind, archive URL or endpoint/request identity (`source_ref`), nullable source hash, `ingestion_run`, raw source timestamp and precision. Derived provenance adds parent_refs and method_version. Hash establishes byte identity, not source completeness/authenticity. Raw generations/source conflicts must remain auditable.
- Availability is exactly AVAILABLE, PARTIAL, UNAVAILABLE, UNVERIFIED, with a reason for the last three. Coverage has its own status, range and evidence_refs. COMPLETE requires evidence and compatible availability; a validator does not authenticate those evidence references. B supplies authoritative coverage proof.
- Pagination evidence includes request_id, endpoint, request_params, request range, input/output cursors, response_count and terminal_condition EXHAUSTED/EMPTY/PARTIAL/FAILED/UNVERIFIED. A pagination record alone can claim only PARTIAL/UNKNOWN coverage; full source-specific completion proof is separate. Empty or exhausted pages never become COMPLETE automatically.
- Footprint uses `side_basis="taker"`, Buy/Sell/Unknown. Never verified Bid/Ask volume. Unknown side retains unknown_side_volume and null verified delta. C owns calculations; E must not reimplement them or derive missing analytics from OHLCV. Partial known-side sums need distinct labels, not verified full delta/CVD.
- Candle is_closed is separate from coverage. Official/derived comparisons require source coverage, boundaries, rounding and tolerance context; absent context means not_comparable, never fabricated match.
- E's existing API envelope retains schema_version/data/next_cursor and adds approved availability/coverage/provenance via an explicit adapter. Earlier `exchange_timestamp_ms`, lowercase availability and `source_refs` models must be mapped to canonical fields. These mappings require executable fixtures, not silent renaming.
- Job progress explicitly names units and nullable total. Unknown total is indeterminate. COMPLETE requires verification of the entire declared work, not just a loop finishing. D shows DATA_UNAVAILABLE plus reason for unavailable panels.

## Ownership and dependencies

A adapters/pagination evidence → B PostgreSQL storage/quality → C calculations → E API/schema adapter → D frontend. A/B/C also provide source metadata to E. SQLite is staging only, PostgreSQL canonical. No hard-coded credentials. A–E must publish callable paths, dataset names, keyset cursor ordering and examples; independent contract gates operate on exact branch SHAs.

GitHub Issues/PRs are authoritative communication. Local temporary reviewers provide supporting evidence and do not imply the five external agents run continuously. MASTER records corrective tasks, dependencies and reviewed outcomes in MASTER_STATUS.md.

## Integration gate

Use isolated branches and Draft PRs. Integrate only reviewed immutable commits, one at a time, with full relevant tests after each. Do not replace an entire baseline with an old workstream snapshot. Main merge, physical deployment, deletion of branches/history/user databases, trading keys/orders, firewall/ACL changes remain prohibited without separate authorization where applicable.

Readiness is NOT_READY whenever a required acceptance gate lacks evidence. No recurring monitoring automation is configured; monitoring is limited to the active session.


