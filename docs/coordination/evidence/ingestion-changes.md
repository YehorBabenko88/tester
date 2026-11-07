# Ingestion fixes and executed evidence

Owned files: `work/repo/engine.py`, `work/repo/test_ingest_integrity.py`. No commits or remote writes made by this agent.

Root causes reproduced first: sparse returned pages were considered complete based on age alone; nonfinite and domain-invalid observations were persisted; archive batch commits preceded gzip EOF validation; ID conflicts ignored changed payloads; clusters included unpublished trades.

Changes: validate finite/domain-correct OI/ratio values and timestamps; compare regular samples to the entire requested grid; funding remains `unknown` without a proven schedule. Existing observation coverage is rechecked because old complete markers carry no versioned sampling proof. Imports hold an instrument/day PostgreSQL transaction advisory lock, stage all rows until validated gzip EOF, reject changed existing ID payloads and nonfinite trades, and publish trades/manifest/complete coverage in one transaction. Clusters require matching manifest hash and complete archive coverage. Filesystem rename happens before transaction commit; rename or commit failure records explicit error coverage. A failed partial file is retained and overwritten only by the next lock holder, avoiding unlink races.

## Commands actually executed

From `work/repo`:

- `../venv/Scripts/python.exe -m unittest -v test_ingest_integrity`: initial run exposed sandbox temp-directory errors alongside genuine failures; temp fixture moved inside the workspace.
- `../venv/Scripts/python.exe -m unittest test_ingest_integrity`: RED, 11 test methods, 13 expected assertion failures, no errors.
- After implementation, `../venv/Scripts/python.exe -m unittest -v test_ingest_integrity`: GREEN, 11 methods, no failures.
- New legacy-cache regression alone: RED, expected client query never happened. After removing reliance on unversioned complete markers, it passes.
- `../venv/Scripts/python.exe -m unittest discover -v`: 27 methods, failed with 28 failures and 3 errors entirely in concurrently edited `test_screener` tests. Ingestion, core and contract methods passed in that run. Screener owner/coordinator informed; this is not a claim of a green complete suite.
- `../venv/Scripts/python.exe -m unittest -v test_ingest_integrity test_core test_contracts`: targeted final verification, 23 methods pass (12 ingestion + 7 core + 4 contracts).

Tests exercise actual parsing/gzip/filesystem behavior using small generated archive payloads and mock HTTP/PostgreSQL boundaries. They verify CRC failure after >1500 staged rows cannot commit, mismatched ID payload rejection, disk rename failure, nonfinite trade rejection, observation schedules/domains, and query publication predicates. Mock SQL assertions do not establish actual PostgreSQL rollback, SQL execution, lock contention or concurrency behavior. A separate storage agent is conducting real isolated PostgreSQL acceptance; its evidence must be reported separately.

