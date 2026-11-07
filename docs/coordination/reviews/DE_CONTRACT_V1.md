# Issue #8 D/E conformance: NOT_READY

Read approved GitHub Issue #8 and branch collection via read-only connector. Heads observed unchanged: D `421f22ba3a2c377c16f08b3f0f5b79417078e98f`; E `36c939fcfb36ec8b8e7e529f1f1cdda8083a3279`. Complete recursive immutable tree snapshots and the exact D frontend file are under work/repo/contract_checks/snapshots. GitHub blob identity in d-tree.json is recorded; snapshot HTML copied verbatim except a final newline.

Added standalone `work/repo/contract_checks/test_de_contract.py`; no __init__.py, ordinary root unittest discovery does not recurse into it. Run explicitly from work/repo:

```
../venv/Scripts/python.exe contract_checks/test_de_contract.py
```

Result: **6 tests, 4 failures, exit 1, overall NOT_READY.** Complete run log saved to `work/repo/docs/coordination/evidence/contract-de.log`.

- D exact script executed in Node v23.6.0 vm with a minimal controlled DOM and fetch. A PARTIAL instrument renders only `FIXTUREUSDT Trading`; PARTIAL and its missing-taker-side reason are discarded. An UNAVAILABLE empty response produces blank output; state and reason are discarded. These fail Issue #8's explicit availability rendering requirement.
- D no-fallback probe passed for this empty fixture: no invented numeric delta/volatility/capitalization was rendered, and only its existing instruments endpoint was requested. This is a narrow observation, not proof that future analytics integration is safe. Snapshot identity check passed.
- E complete tree contains no src/api implementation and no shared contracts schema. API/schema-adoption gate fails explicitly; absence of an API also fails the delegation-review gate instead of vacuously passing “no duplicated analytics.” There is no endpoint to execute schema validation against. No API or substitute success implementation was invented.

Controlled response fixtures use D's existing `{instruments:[...]}` shape, annotated with Issue #8 availability and reason at top level and on the partial row. They are adverse UI probes, not a declaration of an approved wire-envelope shape: Issue #8 specifies semantic fields but no endpoint envelope. Root owns the schema rewrite. Later adoption must rerun against the actual E interface and schema, including response rejection, normalization, and C delegation tests. The log does not claim those missing tests passed.

No live browser, server, PostgreSQL, exchange network calls, remote comments/branches/PRs or production/schema/CI changes were made. Only standalone tests, immutable evidence snapshots, log and this report were added for this task. External workstream chats may have unpushed code not reflected in these immutable public heads; it was not reviewed.

