# Prospective visibility receipts — proposal v0.1

Status: PROPOSED, NOT IMPLEMENTED OR APPROVED. Owner B; MASTER technical review
required before any successful historical-quality certificate or C binding.
Issue #2 marker `master-task:a8aa-prospective-visibility-design`.

## Meaning and alternatives

Recommend an immutable, bounded verified snapshot at a named fence, not a claim
that live storage can reconstruct arbitrary historical as-of state. Per-row commit
timestamps do not by themselves prove negative facts; an after-read wall-clock sample
without a writer fence does not close concurrent changes. Neither is proposed.
A receipt would mean: this exact manifest and persisted validation result were
committed and then observed together no later than `observed_by_ms`. It would NOT
mean exchange acquisition occurred then, that the receipt itself was published by
then, that no later corruption exists, or that an unrelated/current read is covered.
Legacy facts may enter a NEW capture; their earlier availability stays unknown.

## Proposed receipt shape (separate version, no normalized-model pin change)

`protocol= tester.visibility-receipt/1`, receipt_id, capture_id, scope, range,
policy_version, writer_protocol_epoch, fence_generation, snapshot identity,
manifest_sha256, manifest_bytes, validation_result_sha256, fact_count by kind,
negative_predicates with exact bounded universe and policy, archive_mode,
`observed_by_ms`, exact sampled timestamp text and clock-domain assumption.
`observed_by_ms` uses ceiling(epoch microseconds / 1000), never floor, preserving
original timestamp text. No market/source precision is modified. Clock trust and
rollback handling are preconditions; without a trusted UTC upper-bound clock domain,
wall-time certification is UNAVAILABLE. Logical generations alone cannot prove UTC.

## Proposed capture protocol

Preconditions include verified durable PostgreSQL configuration/storage and
synchronous commit acknowledgement for capture and receipt. Async commit, disabled
fsync/full-page protection, uncertain restore lineage or storage guarantees leave
certification UNAVAILABLE. This proposal authorizes no global setting changes;
configuration checks/enforcement and restore epoch rotation require review. Database
restore/clone must change the writer epoch so old receipts cannot certify new live state.

1. Establish a versioned writer epoch covering EVERY mutator of scoped raw,
   source, observation, run links, evidence, coverage, findings/resolutions,
   quarantine and archive registrations; cross-scope mutations take gates in a
   deterministic order. Enforce participation through future DB privileges and
   controlled write entrypoints, not advisory-lock convention alone. Bypass writers,
   maintenance/DDL or missing negative-fact inventories invalidate the epoch.
2. On a dedicated session acquire the exclusive session gate OUTSIDE any MVCC
   capture transaction. Waiting transactions must not have acquired a snapshot.
   Only after acquisition begin a fresh RR transaction; no old transaction or
   snapshot imported from before the fence is permitted. Hold the session gate
   through capture commit, confirmation and receipt publication. Writer gates are
   held through commit/rollback, not just the last INSERT.
3. Materialize bounded immutable manifest AND validation result atomically. Include
   all rows (or exact page membership), raw hashes, full provenance and run edges,
   all relevant evidence/intervals/findings/resolutions/quarantine, policy/schema
   versions, and exhaustive declared predicate universes. Include zero-match
   findings/quarantine checks as explicit predicates, not inferred missing history.
   This observes a closed state NOW; no assertion about pre-epoch absence follows.
   Missing joins, missing inventory, overflow, timeout, unsupported facts or partial
   pagination fail the capture with no certificate. An empty capture may describe
   emptiness, but cannot authorize nonempty trade analytics or COMPLETE history.
4. Commit that manifest/result. In a NEW transaction on the still-gated session,
   re-read the committed capture by hash and validate complete manifest membership.
   Sample the trusted upper-bound clock only AFTER confirming durable visibility.
   A delayed capture commit therefore moves the bound later. Build an immutable
   receipt binding the exact capture hash/generation and this newly observed bound;
   persist atomically. Receipt cannot certify partial or uncommitted capture data.
5. Return a receipt only after receipt commit is acknowledged and a fresh lookup
   verifies it. A crash at any earlier step leaves UNAVAILABLE. Orphan captures are
   inert; retry performs a NEW gated confirmation and uses a NEW bound, never
   guessing a pre-crash clock. Receipt persistence after sampled time does not
   backdate receipt publication: only the already durable capture/result is covered.
6. Consumers read the immutable capture by receipt identity and verify all hashes,
   counts, versions, scope/range/cursor and result binding. They may NOT attach its
   bound to a new live `read_page`, mix pages/captures, or silently refresh facts.
   Later mutations require a new generation/capture for current quality. An older
   receipt can describe only its named snapshot; a request for current freshness
   rejects it when generation differs. No arbitrary cutoff reconstruction follows.

## Archive and negative-fact boundaries

External paths can change outside PostgreSQL locks. Proposal v1 defaults to
`archive_mode=DATABASE_FACTS_ONLY` and claims NO historical physical-file health.
If physical health is required for quality, certification remains UNAVAILABLE until
an enforced immutable retained content-addressed byte store and complete lifetime
inventory are reviewed. A one-time hash/stat or filesystem mtime is insufficient.
If immutable bytes become part of a capture, exact bytes/hashes, retention and
reader verification must be bound to that capture, not its mutable original path.

Negative predicates concern the captured, enumerated database universe under the
writer epoch. They do not claim the absence of unreported exchange defects,
out-of-band file damage, or historical incidents before enrollment. If C's requested
quality semantics require those stronger claims, this proposal cannot satisfy them.

## Review gates and executable RED cases

No production code/migrations/writer gates/receipt success path are changed here.
`knowledge_time.status` remains UNAVAILABLE. MASTER must review especially:
DB enforcement of all writer participation; clock trust; negative predicate universe;
archive exclusion; whether C may map the persisted snapshot validation bound to
quality_available_at_ms without implying receipt publication or historical hindsight.
No such mapping or provider default is authorized in this proposal.

`tests/storage/visibility_protocol_cases.py` is a pure ordered-event specification
probe, NOT a receipt validator or a PostgreSQL simulator. It rejects named unsafe
traces. `--demonstrate-red` substitutes the deliberately unsafe sampled-time-only
candidate: assertions must fail. Unit tests check each counterexample is rejected,
including stale RR, delayed commit, late facts, missing negative inventory, crashes,
stale reuse, archive mutation and resource overflow. MASTER's retained-DB
interleavings must separately establish actual lock, MVCC, crash and durability
behavior before implementing a successful production certificate.

## Limited foundation review checkpoint

MASTER reviewed89221 in Issue2 comment6711209526 and authorized only the isolated
schema/epoch/common-gate prototype, not this proposal's successful certificate path.
Implementation boundaries/inventory are in visibility-foundation.md. Capture-bound
and later receipt-publication observation must stay distinct: a C snapshot carrying
a later receipt cannot use the earlier capture bound for the whole quality result.
UTC clock uncertainty, trusted external restore enrollment and all-mutator DB
closure remain separate unimplemented gates. Production remains UNAVAILABLE/null.
