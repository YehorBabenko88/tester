# Bybit ingestion implementation plan

Goal: reliable read-only official archive and REST ingestion, with explicit evidence and unresolved coverage.
Architecture: requests transport with shared throttling; SQLite task/provenance journal; validated atomic archives; endpoint-specific REST pagination; JSONL export for B.
Spec: user mission P0–P4 in this chat. Python 3.11+, existing requests dependency. Own src/ingest, tests/ingest, docs/ingest only; leave database and other workstreams untouched.

Ruling: autonomous execution requested; implement inline in dedicated clone on workstream/a-ingest. No extra worktree or design approval needed.

- [x] Audit official documentation and archive listings; persist timestamped evidence, earliest observed dates and unknown global boundaries.
- [x] Test transport retry and rate limit behavior, archive discovery, corruption, HTTP Range semantics, restart. Implement transport.py, journal.py, archive.py; concurrency implemented and opt-in live test provided.
- [x] Test REST cursor and time pagination, empty-window probes, duplicate/non-progress detection and gap repair. Implement rest.py with persistent per-window checkpoints.
- [x] Test instrument pagination and verified-window volatility; implement catalog.py and CLI.
- [x] Run all tests and real archive/REST/restart smoke checks; document constraints and integration contract; independent review and Draft PR to main.

Review focus: server ignores Range; object changes during resume; truncated gzip; empty REST page in middle of history; cursor loop; calendar months; missing/unclosed candles; evidence must never imply global completeness.

Ledger: 27 unit tests passing. Real REST 14 dataset/category cases, 5 archive samples and screener retrieved; MT4 extra sample downloaded. Independent reviewer found funding/closure/resume issues; each reproduced RED then fixed GREEN. Later live rerun blocked by OS DNS; preserve successful timestamped evidence rather than relabel outage as empty data. GitHub Issue #7 carries B/E contract; no storage files changed. No merge authorized or performed.
