# Bounded consistent read page and active proof

Additive API for Issue2 comment6709223310. Existing `read`, `provenance`, `coverage` and `coverage_evidence` semantics are unchanged. No migration or data rewrite is needed. MASTER/E should explicitly opt into this API instead of combining separate legacy reads.

```python
page = store.read_page(
    dataset, venue, category, symbol, start_ms, end_ms,
    interval='', limit=1000, after=None,
    max_observations=4096, max_run_links=4096, max_evidence=256,
    max_intervals=512, max_findings=256,
    max_bytes=4*1024*1024, max_archive_bytes=16*1024*1024,
    statement_timeout_ms=3000,
)
```

Scope/range/cursor/limit validation happens before querying. Range is UTC half-open `[start_ms,end_ms)` and cursor is `[timestamp_ms,record_key]`, an exact int64 and lowercase64-character SHA key inside that range. Sort is `(timestamp_ms,record_key)`, including equal-ms trades. `next_cursor` is supplied only when more records exist in this snapshot. Every call has a new snapshot; this is stable keyset ordering, **not a snapshot pinned across HTTP pages**. A later backfill before a prior cursor requires revisiting the requested range.

## Return contract

```text
{
  outcome: "OK" | "RESOURCE_EXHAUSTED" | "UNAVAILABLE",
  scope: {venue,category,symbol,dataset,interval},
  rows: [normalized raw records with decimal strings],
  observations: [{record_key, source_id, source, raw_envelope, observation_id,
                  run_id, job_key, evidence_id, source_kind, timestamp_precision,
                  run_origin, source_run_status, evidence_run_status, authentic_run}],
  run_links: [{run_id, job_key, evidence_id, run_origin, evidence_run_status}],
  evidence: [{evidence_id,job_key,scope_key,start_ms,end_ms,status,record_count,
              verified,validator_version,created_at,evidence,active_proof,
              within_read_horizon,horizon_rejections}],
  next_cursor: [timestamp_ms,record_key] | null,
  page_exhausted: boolean,
  coverage: {start_ms,end_ms,complete,state:"COMPLETE"|"UNKNOWN",
             proof_refs:[evidence_id],reasons:[],intervals:{...interval unions...}},
  snapshot: {isolation:"repeatable read",started_at:UTC timestamp,xmin:string,clock_ms:int64} | null,
  limits: {...effective limits...,max_fetched_bytes:2*max_bytes},
  usage: {rows,observations,run_links,evidence,intervals,findings,archives,
          bytes,fetched_bytes,archive_bytes}
}
```

**OK means the bounded read finished, not that the range is complete.** Only `coverage.complete` with nonempty `coverage.proof_refs` is a successful authenticated range proof. `evidence[].verified` is original immutable ledger metadata, not a current certification. `active_proof` is true only for refs actually supporting a successful range proof. Interval ratios/verified duration are diagnostics and are not an alternate completion flag. Partial page pagination is independent of full requested-range proof; ordinary `next_cursor` does not invalidate otherwise proven storage coverage. An empty page never receives a COMPLETE certificate, even if the caller's cursor is past existing records.

All bounded source observations are returned, including historical/unverified observations and missing-link placeholders. Run/evidence edges are separate, so EMPTY acquisition evidence retains its actual run IDs even without records; these are never promoted to proof. No read invents source/run identity. `authentic_run` here is stricter than the legacy resolver: original `source.ingestion_run` must be explicitly matched, caller origin and source kind known, raw/source identity/content/precision valid, and any evidence ingestion_run declaration noncontradictory.

On **RESOURCE_EXHAUSTED**, all rows/observations/run_links/evidence and proof refs are empty, next_cursor is null, page_exhausted=false, complete=false and reasons name the cap. This is not an empty acquisition or a successful truncated page. Increase permitted budgets or narrow the requested time window; shrinking only page size cannot remove fan-out of one historical record. If a hard ceiling is reached, the range remains unavailable through this bounded API; it does not discard history to manufacture a proof. **UNAVAILABLE** covers query deadline/cancellation, database read failure or malformed documents that cannot be safely decoded. Invalid caller parameters raise ValueError.

## One snapshot, active proof rules

One connection/transaction uses REPEATABLE READ READ ONLY, local work_mem4MB and remaining query deadline. Page, observations, run/evidence links, coverage, resolutions, findings and archive manifests are read through that same transaction. A writer may commit while the reader is running: the response describes its original snapshot. A late resolution does not retroactively certify an old read; a later read sees it. Snapshot metadata is diagnostic, not a freshness lease, and cannot guarantee that no writer commits after the response.

### Authoritative elapsed-history cutoff (P1, PR13 comment6709945814)

`snapshot.clock_ms` is captured once from PostgreSQL `floor(extract(epoch FROM transaction_timestamp())*1000)::bigint`, the UTC start of this read transaction. All certification checks use that same integer cutoff; neither client time, source fetched_at, operator assertions nor a later clock sampled at the end can advance it. For a half-open range, `0 <= start_ms < end_ms <= snapshot.clock_ms` is required; equality at end is elapsed and permitted. Existing candle closure/grace checks remain additional requirements.

The **entire caller-requested range**, **entire candidate evidence range** (both stored metadata and body) and any declared **entire available_range** must have elapsed. A future candidate cannot certify a historical subrange by clipping, and a future availability claim cannot certify a smaller elapsed candidate. Future claims remain immutable audit data with `within_read_horizon=false`, explicit horizon_rejections and `active_proof=false`. Invalid candidates cannot resolve findings either. Rejected requests retain their full original requested bounds, UNKNOWN and no authoritative proof_refs; they are never silently shortened to the present. An independently valid elapsed candidate may support an elapsed request; rejected audit claims are not promoted. `within_read_horizon=true` only means the temporal boundary check passed, not that the evidence is a complete/active proof.

Five actual-PG regressions cover the original one-trade/operator-claim-to2100 case, historical clipping of a future candidate, future request with a historical candidate, future availability with historical candidate and elapsed/exact boundary. The future test also spoofs the client clock to2200 while the captured DB clock remains2026. Historical candle/OI/funding positives stay green. No legacy read/write semantics, source declarations, schema or stored claims are rewritten.

Proof candidates require EXHAUSTED + verified + known validator, matching scope/bounds/count/body metadata, explicit availability document, actual original-run matched support and raw/source identity checks. Full supporting observations outside the requested range are also read when a candidate proof spans a larger window; they share the same total caps. A newly confirmed raw record missing from that proof invalidates the old full-window proof. A legacy `checks.contiguous=true` is insufficient: actual supported candle boundaries/calendar closure, OI/ratio cadence, funding inventory and exchange-origin trade IDs are rechecked.

Available/verified intervals are usable only with valid candidates. Unresolved unknown/corrupt/unavailable intervals/findings block their range. A resolution requires a valid proof covering the whole incident and evidence created no earlier than the incident, with resolution recorded no earlier than that evidence. Historical bad evidence is audit-only. Raw/source/evidence/archive logical quarantine blocks certification; restoring matching archive bytes alone does not clear quarantine. Archive-manifest proofs also require matching URL/SHA/count/full bounds, actual file SHA and stable size/mtime/inode across hashing.

COMPLETE needs the union of authenticated active available+verified intervals to cover **exactly the caller's requested range**, with no active blockers/conflicts. There is no automatic clipping to an available subrange to show100%. Operator/exchange availability attestations retain their existing trust boundary; this is not proof of exhaustive exchange-wide history.

## Resource ceilings and precise byte meanings

| Limit | Default | Allowed range |
|---|---:|---:|
| trade/raw page rows |1000|1–1000|
| observations, including missing-link rows and outside-window proof support |4096|1–10000|
| run/evidence edges, including EMPTY acquisitions |4096|1–10000|
| evidence documents |256|1–512|
| coverage intervals, including history needed to evaluate resolution |512|1–2048|
| findings + logical quarantine rows |256|1–512|
| max_bytes, retained serialized documents and final compact UTF-8 response ceiling |4MiB|8192–16MiB|
| max_archive_bytes, total file bytes hashed |16MiB|0–1GiB|
| cooperative SQL/FETCH/file-chunk deadline |3000ms|100–10000ms|

Archive manifest rows share a separate ceiling equal to max_evidence. Evidence/observations/edges use bounded SQL LIMITs, server-side named cursors and FETCH1. No unbounded fetchall or jsonb_agg is used. SQL suppresses an oversized single document and the overflow row's body before client transmission. Retained document bytes are charged together with a4096-byte envelope reserve; the final response is measured again. One transient fetched row can exceed the remaining retained budget, so aggregate fetched document bytes have an explicit independent ceiling of2×max_bytes. `usage.bytes` is charged intermediate document bytes, not final response length; `fetched_bytes` measures actual encoded document bodies, excluding wire protocol overhead. Counts never include a full over-cap payload; errors return no partial certificate.

These are serialized document/count bounds, not an absolute Python/PostgreSQL RSS promise. Python object overhead and PostgreSQL per-value JSON/de-TOAST allocations are not governed by work_mem; work_mem bounds executor sort/hash working memory. Connection timeout and a blocking local filesystem call are not preempted by the cooperative deadline; elapsed time is checked before/after queries/chunks and before certification. UNC share reads are not used. No file is downloaded or changed. Additional role/OS memory/I/O admission controls remain deployment concerns.

## Verification and existing safety

Actual-PG regressions cover full matched proof, equal-ms keysets, observation/evidence/interval/finding/run-edge/byte caps, EMPTY audit retention, partial/undeclared data, later damage and later resolution during an in-flight read, stale proof after new raw, archive SHA/byte caps/quarantine restoration, old proof resolving a newer incident, historical evidence-run conflicts, false historical cadence flags and nested malformed source handling. The malformed fixture also exposed an existing lifecycle scan recursion escape; a one-line defensive catch now reports raw_envelope_invalid, with no scan policy/deletion changes.

Independent reviewer reproduced material edge cases and rereviewed fixes. Its independent full-suite runner could not reach PostgreSQL (TCP10013 in that tool context), so that run is not reported as passing. The reviewer separately authored `tests/storage/independent_read_page_check.py`; root executed all3 literal fixtures against real PostgreSQL successfully (positive strict proof, observation fanout failure, old-proof/later-incident rejection). Run it explicitly with a disposable TESTER_TEST_ADMIN_DSN using `python -m tests.storage.independent_read_page_check`; it creates/drops only its own randomly named test DB. It does not execute on import or ordinary unittest discovery.

Latest P1 horizon verification: **81 branch tests/15.596s/OK/zero skips**, including all prior76; **90 main+B snapshot tests/15.787s/OK/zero skips**, no merge. The unchanged MASTER adverse probe passed (1 actual-PG test/0.463s): original full bounds to2100 retained, UNKNOWN, verified_ms0, percent0 and proof_refs[]. Updated modules were preloaded because the original probe embeds its old checkout path; the probe body/assertion was not edited. The3 reviewer-authored checks also passed again. Narrow independent code review found no material issue in the horizon fix.

PostgreSQL18.4/Python3.14/Windows, durability on. These are root-executed full suites/reproductions, not a claim of an independent full-suite pass. Exact HEAD and full logs are reported in PR13/Issue2. No user DB migration, physical deploy, trading or raw deletion occurs in this work.

## Knowledge-time projection v1 (code-only follow-up)

`read_page` adds `knowledge_time` from the same bounded repeatable-read fetches.
`facts` contains the fact kind, identifiers and original PostgreSQL timestamp strings
(raw inserted_at, source/observation recorded_at, run created_at, evidence/interval/
finding created_at, resolution resolved_at and archive created_at where consulted).
No second query or historical-as-of filtering is introduced. Unpaged support records
are included through their existing observation fetch. Absent run-evidence/run-job
link timestamps and quarantine timestamps are explicit nulls. Missing negative-fact
history (for example absence of corruption) is also not certified.

All currently supported schema versions use transaction-start DEFAULT now(), not a
trusted commit/visibility upper bound. Therefore v1 deliberately returns
`status: UNAVAILABLE`, `quality_available_at_ms: null`, `historical_as_of: false`.
Neither fetched_at, market time, read clock, task completion nor a client cutoff is
substituted. Original timestamp strings retain sub-millisecond database precision;
no integer milliseconds or acquisition precision are fabricated. Consumers must
reject historical quality binding when this value is null, even if present-time
coverage is COMPLETE. Availability and coverage are distinct contracts.

Projection documents consume the shared retained byte budget and final serialized
response ceiling; all existing document caps and deadlines remain. Resource/database
failures return UNKNOWN/no proof and discard the partial knowledge facts. Empty data
has unavailable knowledge time. No migration or writer change is included: providing
historical availability requires a separately designed trustworthy persisted
visibility protocol; max(transaction-start timestamps) would be unsound.
