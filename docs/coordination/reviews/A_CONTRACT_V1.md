# Original A contract v1 gate

Read approved [Issue #8](https://github.com/YehorBabenko88/tester/issues/8) through the GitHub connector. Added only opt-in `work/repo/contract_checks/check_a.py`; no changes to the A adapter, engine, branches, comments or remote state.

The gate loads the exact original `src/ingest/archive.py` bytes through read-only `git show 43d0126d28c95f8f5f6362e866bd6e6d50a5e7bb:src/ingest/archive.py`, executes them as an isolated module, and records the source SHA-256 in machine-readable output. No prospective adapter function names are invented. The only actual source exports are `archive_url` and `download`.

Executed from `work/repo`:

`../venv/Scripts/python.exe contract_checks/check_a.py --json ../contract-a-result.json`

**Exit 1: 7 methods ran, 1 passed, 6 failed, 0 errors, 0 skips.** Expected nonconformance remains visible; it is not patched or marked as a passing expected failure.

- PASS: exported downloader returns exact byte count, URL and SHA-256 for a generated gzip fixture.
- FAIL: exported metadata has no `schema_version`.
- FAIL: URL/hash metadata lacks required provenance context (source kind, ingestion run, source timestamp precision).
- FAIL: HTTP 200 HTML overwrites a pre-existing archive.
- FAIL, explicit UNAVAILABLE: no exported REST adapter exists to exercise request boundaries, cursors, response counts or pagination terminal conditions, including empty-page behavior.
- FAIL, explicit UNAVAILABLE: no exported record adapter can demonstrate mandatory integer `timestamp_ms=1700000000123` from the source string `1700000000.123456789`.
- FAIL, explicit UNAVAILABLE: no exported record adapter can demonstrate genuine source-provided `timestamp_ns=1700000000123456789`, preserve its nine decimal places without float conversion, or avoid inventing precision for millisecond-only sources.

Machine-readable result: `work/contract-a-result.json`, including exact pinned SHA, source path/hash and all unittest failure tracebacks. Timestamp and pagination failures report absent capabilities rather than pretend to have executed an undocumented adapter. The fixture's fractional source seconds provide real sub-ms precision; conversion from a millisecond integer would not satisfy this gate.

The check is opt-in (`check_a.py`, not `test_*.py`), so intentionally red original-branch acceptance does not contaminate normal coordinator test discovery. HTTP is mocked; gzip/filesystem/downloader behavior is real. No live network data, PostgreSQL or branch mutation is involved.

