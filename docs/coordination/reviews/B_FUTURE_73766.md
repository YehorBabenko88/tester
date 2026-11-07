# Independent B future-horizon follow-up

Reviewed exact tester commit 73766dd531387f660ddfb243b64d28be9389ddd1 against dc545c0a9857f279df46c3a9048871bb3232031f. Read-only source review, plus pure checks against helper functions extracted in memory by AST from the exact Git blob. No worktree/branch changes, PostgreSQL/SCM/elevation/physical-root access or database writes.

## Assessment

The identified future-range COMPLETE defect is addressed in the new bounded read_page path:

- One clock_ms is captured from `floor(extract(epoch FROM transaction_timestamp())*1000)::bigint` in the same query as snapshot metadata. Subsequent event/candle and horizon checks use that database value; the old Python wall-clock read was removed. Monotonic time remains only for resource deadlines.
- within_horizon requires actual integers and `0 <= start < end <= clock_ms`. Equality at the elapsed endpoint is allowed; +1ms is rejected. It separately checks the requested range, candidate row range, evidence-body range and stated available range.
- An unelapsed candidate is excluded before valid proof admission. Such evidence cannot authenticate an old incident's resolution or certify a historical subrange through clipping. An unelapsed requested range cannot become COMPLETE even if it intersects an otherwise elapsed valid candidate.
- Original requested/evidence/availability endpoints remain unchanged for audit. Horizon rejections and within_read_horizon are explicit; active proof flags and proof_refs are withheld when overall completeness fails. No endpoint clipping or range-denominator substitution was introduced.
- The previous whole-batch support, source/run relations, cadence, active incident chronology and quarantine checks remain in the admission path. Empty pages still cannot become COMPLETE. Legacy read/provenance/coverage methods are unchanged; consumers must not assume the old API acquired the new guarantee.

No new material correctness defect was found in this narrow change. This is source/pure-test review, not an independently executed PostgreSQL acceptance result.

## Executed evidence

Ran 13 direct helper cases against exact 73766 Git source compiled in memory: elapsed equality, future end/start, negative/empty ranges, bool/float rejection, clean proof, independently future outer/body/availability bounds, malformed availability/body, and preservation of supplied endpoints. **13 checks PASS.** No provider code or reference files were edited.

Inspected five added PostgreSQL scenarios in tests/storage/test_read_page.py: future requested range retains its original claim, future candidate cannot certify a historical subrange, future request cannot reuse an elapsed candidate, future availability invalidates an elapsed candidate, and elapsed boundary uses database clock even with a deliberately wrong Python wall clock. These tests were inspected, **not run against PostgreSQL by this reviewer**.

## Remaining scope limits

This change does not authenticate an operator's attestation against the exchange, guarantee local database clock synchronization, provide historical as-of snapshots, or integrate MASTER/E with the new proof contract. Admission still relies on source/evidence authenticity plus the existing integrity checks. A database clock incorrectly far ahead can make chronological assertions appear elapsed; deployment clock health is a separate operational obligation. No full-pipeline, live-source completeness or deployment approval follows from this review.
