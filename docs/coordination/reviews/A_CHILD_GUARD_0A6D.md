# A child-guard correction review

Exact HEAD **0a6d07c0aa65037a67320ae30533471186f1ed88**, diff against **467774de7ca67fe9927695dac3fd9760cabdcb00**. Only runner/tests/docs changed; production A3098/B-e236/shared9766 pins are unchanged. Reviewed exact fetched Git diff and root-owned `work/a-guard-check` snapshot read-only.

**Previous hidden restart-child P2 is resolved. Bounded approval for root's normal-parent isolated replay; no new material defect found in this correction.** This is not PostgreSQL execution/acceptance evidence.

- Both `child_main` modes call the same `guard_child_context` before constructing Store, loading/running fixture test cases, or performing data reads/writes. Normal fixture setUpClass also uses the guard.
- Both modes validate parent-supplied pins.json digest, expected fixed source SHAs, nonempty snapshot file inventory, resolved path containment and every source file hash. Exact imported module files must correspond to pinned manifest paths. An unknown/unpinned module cannot reach fixture work.
- Before application work, both check connected `SHOW data_directory` equals the unique scratch root/pgdata. This read-only identity query is the guard itself, not an allowed data access before validation. A misdirected DSN rejects before Store/DDL.
- Restart mode additionally validates the parent-supplied restart-state digest, pair_ plus32hex fixture DB name, exact expected fixture storage directory and exact reopened handoff path. It then uses the guarded admin DSN and guarded Store module; the former unchecked direct restart path is gone.
- Parent captures pins digest before first child and restart digest after initial tests/before stop-start. Shared guard explicitly targets accidental/misdirected execution, not an attacker controlling parent environment and files. That limitation is correctly stated; no hostile sandbox guarantee is made.
- Existing real startup/cleanup implementation is unchanged. No directory deletion or user database operation was introduced by the patch.

## Actual independent execution

From `work/a-guard-check`, root venv with `tempfile.tempdir=os.getcwd()`:

1. `unittest` load `tests.ingest.test_child_guards`: **2 methods PASS**, exit0. These use mocked psycopg/imports and real temporary manifest/source files. Subcases cover both child modes, parent digest mismatch, wrong fixed pin, changed source hash, wrong import origin, external data_directory, restart metadata digest and forged DB/storage/handoff metadata. Assertions prohibit Store construction, prohibit test-suite loading after guard failure, and restrict mocked SQL to SHOW data_directory.
2. Full ordinary unittest discovery: **44 methods PASS**, exit0. The opt-in pinned_roundtrip module is not auto-executed by test discovery.

No PostgreSQL server connection, initdb, CREATE/DROP DATABASE, migration, pg_ctl start/stop or destructive operation was executed. Author's six PG cases/restart/cleanup logs remain inspected claims only. Root's normal-parent replay on its new owned cluster is the separate remaining execution gate. No product/source/remote edits or cross-repository dependencies were made.
