# Master integration implementation plan

> For agentic workers: use executing-plans and test-driven-development; independent reviewers inspect frozen workstream SHAs.

Goal: coordinate five workstreams and produce a reproducible, evidence-backed integration candidate; readiness stays NOT_READY until every acceptance gate passes.

Architecture: preserve the existing Python/PostgreSQL research application. Review workstream additions against a pinned base, publish versioned JSON Schema interchange contracts, then integrate only reviewed commits on integration/master. No automatic main merge.

Spec: user-provided MASTER CODEX directive, reproduced in Issue #6 scope and AGENT_PROTOCOL.md.

Constraints: only YehorBabenko88/tester; no trading keys/orders, existing database deletion, firewall changes or office deployment. Five workstream branches are owned by their agents. Windows physical acceptance requires separately recorded execution.

Review focus: false completeness from sparse data; failed gzip imports leaving analytic rows; revised IDs with stale provenance; numeric nonfinite values/precision; UI/API compatibility and tests silently skipped.

## Task 1: Coordination and interchange contracts

Files: docs/coordination/AGENT_PROTOCOL.md, MASTER_STATUS.md, contracts/v1/market.schema.json, test_contracts.py.
Produces: JSON Schema Draft 2020-12 records for instrument/trade/candle/observation/provenance/coverage/download progress/error/API envelope; adoption requires explicit SHA-linked agent acknowledgement.

- [ ] Write schema acceptance tests rejecting mixed timestamp units, float financial fields, invalid market and malformed progress, with ten distinct synthetic instrument records.
- [ ] Run test_contracts.py and observe missing-schema failure.
- [ ] Publish version 1.0.0 definitions and fixtures; run the tests until green.
- [ ] Publish protocol and concrete corrective tasks in Issues #1–#5.

## Task 2: Fail-closed ingestion integration

Files: engine.py, test_ingest_integrity.py.
Consumes: current db.py transaction contexts; existing coverage states, extending with unknown where completeness cannot be proved.
Produces: retryable sparse OI coverage, finite observation/trade validation, atomic archive publication and aggregate eligibility checks.

- [ ] Add and run failing regressions for sparse OI, invalid numbers, failed gzip parsing/transaction rollback and revised archive conflicts.
- [ ] Implement conservative coverage and transactional archive publication without intermediate commits; serialize same-day imports.
- [ ] Run all repository tests after each integration change and preserve evidence.
- [ ] Document remaining real PostgreSQL/recovery and file/database crash-boundary limitations.

## Task 3: Evidence and integration delivery

Files: docs/coordination/reviews/*.md, docs/coordination/evidence/*, MASTER_STATUS.md.
Consumes: exact reviewed SHAs, commands and logs; produces a Draft integration PR, never main merge.

- [ ] Run baseline and expanded unit suites, syntax checks, schema validation and available public Bybit probes. Distinguish synthetic and live data.
- [ ] Inspect PostgreSQL availability without using existing databases; only use a disposable dedicated test instance if available.
- [ ] Independently review the candidate; record defects and unexecuted acceptance gates.
- [ ] Commit on coordination/master; integrate exact coordinator commit on integration/master, test, and publish Draft PR/evidence.

Pre-flight: Tasks 1/2 share state vocabulary. Unknown is retryable and never counted complete; contract adoption remains proposed until agents explicitly confirm. Task 3 consumes evidence from both tasks; unit fixtures do not imply end-to-end acceptance.

