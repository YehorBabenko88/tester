# Independent same-checkout composition review

Reviewed actual mutable work/master-supervisor on integration/pinned-candidate-20261107, HEAD c112bac (B e236395 lineage merged; E b15b1ce lineage composed). Also read pending changes to storage workflow, pyproject, root requirements-dev, tests/test_quality and new tests/integration/test_trade_http. No source, Git or remote modifications; did not run PG tests concurrently with root.

## Concrete gaps

1. **[P2] New B→factory→E HTTP gate is skipped in all current CI routes.** tests/integration/test_trade_http.ReaderHTTP inherits PostgresTests' class skipUnless(TESTER_TEST_ADMIN_DSN). Storage workflow sets that admin DSN but now restricts discovery to tests/storage, excluding tests/integration. Platform workflow sets TEST_DATABASE_URL only, so its whole pytest run skips ReaderHTTP. Root tests workflow has a different BYBIT_TEST_POSTGRES job running only root test_postgres_integration. Confirmed locally `python -m unittest tests.integration.test_trade_http -v` collects the ten-instrument test but skips it without TESTER_TEST_ADMIN_DSN. Add an explicit same-checkout integration step under a fresh disposable administrator DSN (and API dependencies), or a dedicated job; do not count a skipped new test as composed HTTP acceptance. Root's actual local PG run can independently establish current behavior, but CI regression protection remains absent.

2. **[P2] Wheel includes B bootstrap CLI but omits its declared keyring dependency.** pyproject runtime dependencies include psycopg/API libraries but not src/storage/requirements.txt's keyring>=25,<26. src/storage/bootstrap.py:14 imports keyring inside SecretStore initialization; bootstrap uses SecretStore by default, and storage.cli bootstrap calls that path. An isolated wheel import smoke test does not initialize this dependency. Current root venv also has no keyring. Add a supported base/optional storage-bootstrap dependency with matching install instructions; preserve fail-closed OS-backed backend enforcement. This does not affect read-only Store/StorageTradeReader if B is separately provisioned, so it is not a claim the trade HTTP binding itself fails.

## Merge resolutions / packaging assessment

- Retained src/quality/candles.py is B's Decimal/UTC version, rejects float/bool input, exposes local validity and always complete=False. This is the correct resolution; do not restore E's old float/local-validity→COMPLETE implementation just to satisfy obsolete test expectations.
- Updated tests/test_quality.py correctly asserts locally_valid and never complete for closed candles; gap assertion uses unknown half-open[120000,180000). B's own precision/UTC/local-gap tests agree. Existing unclosed test still only checks always-false complete; B's other tests check actual unclosed errors, so no claim this single assertion alone proves closure validation.
- pyproject now includes src.storage, src.quality and src.integration plus B SQL migration package-data and v1_1 price schema. These are the needed same-wheel paths for the internal B→E adapter. No foreign-repository dependency was added. Root reported isolated-wheel import checks; I reviewed declarations but did not independently rebuild/reinstall wheel.
- .gitignore preserves root state/lock/env/cache protections and adds build/dist/egg-info ignores. E runtime/.scratch protection remains in their own tracked .gitignore files; no conflict marker found in inspected files.
- Storage CI's restricted discovery is sensible for B-only dependency installation and avoids accidental FastAPI imports from platform tests; it needs a separate integration gate rather than broadening back to all tests without dependencies. requirements-dev's new API imports fix root discovery needs; root workflow uses unittest, while platform pytest remains a separate acceptance entrypoint.

## Actual checks

- `../venv/Scripts/python.exe -m unittest tests.test_quality -v`: **3 PASS**.
- `../venv/Scripts/python.exe -m unittest discover -s tests/storage -p test_models.py -v`: **12 PASS**, including exact precision, bad OHLC not rounded away, Monday/week/month and unknown sparse gaps.
- New same-checkout HTTP test collection: **1 SKIP** without disposable admin DSN. No PG calls performed by this reviewer; root owns current DB run.
- Attempted Ruff read-only check: dependency ruff absent in review runtime, so no lint/format PASS claimed. Platform CI installs its dev extra and should run its own required lint/format/security checks. Existing scripts under the configured paths are E's formatted scripts; package/storage source lies outside current E Ruff scope by design.

New HTTP test itself exercises real Store ingestion/provenance, selected owned factory, E scope/cursor wrapper, two same-timestamp IDs across ten instruments, exact decimal, acquisition run linkage, unknown coverage, cross-symbol token rejection and unsupported-resource503. Its inherited database lifecycle creates a dedicated randomized tester_b_test_* database and cleans it; no adoption of user databases seen. It is meaningful integration coverage once explicitly enabled.
