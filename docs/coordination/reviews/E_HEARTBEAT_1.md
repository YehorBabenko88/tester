# E heartbeat 1 — prior metadata P1 fixed; request-scope P1 remains

Reviewed exact published E PR9 HEAD **6d8b1b97433fd850a8900ad8a3750b682319dc3a**, complete immutable GitHub tree and compare vs c2fb7b66f9ebc0b4b04519ea327c3baafc1e5f54 (two commits ahead). Isolated source work/e-heartbeat1. No service installation, remote mutations, source fixes or D edits. Scratch fixtures/probes only. Vendored contract bytes refetched verbatim for hash verification after initial scratch line-ending/trailing-newline normalization; final check_contract_pin passes pinned source9766c009. No contract-integrity defect is claimed from the initial scratch-copy mismatch.

## Prior blockers resolved

- CanonicalRecord/ Dataset RootModels now use shared pinned validate_record/validate_response (src/api/canonical.py). All canonical fields round-trip unchanged; no simplified string provenance conversion/metadata stripping. Exact independent fixture returned lossless=True through actual route endpoint then Dataset JSON serialization.
- NaN financial values rejected; AVAILABLE and COMPLETE envelope promotion over partial row rejected. Existing tests cover strict decimals/version, ms-only timestamps with null ns, duplicate IDs, footprint/observation/candle lossless roundtrip. These are executed tests, not inferred from PR claims.
- Worker checks stop before claim and after claim; old no-claim-after-stop gap addressed. DB control singleton locks admission and enforces pending/running budgets, drain state and per-symbol-running exclusivity. Central logging now restricts event identifiers/allowed scalar fields rather than passing arbitrary message strings. Previously identified raw event message leak is fixed in inspected implementation and runtime-review tests.
- SCM plans/native host scaffolding exist; actual install plan generation succeeded and explicitly returns DRY_RUN/physical_changes=false. It declares delayed start, virtual service account, finite restart policy and ACL plan, with VM startup/reboot/DLL/access gates unverified. Plans are not evidence a Windows Service was installed or works under SCM.

## New concrete blocker

**[P1] Valid canonical rows are not bound to the requested market/range/interval.** src/api/main.py:179-185 validates standalone dataset structure, special observation kind and count, but never compares query.category, query.symbol, UTC requested bounds or query.interval against rows/coverage. Independently invoked the actual `/api/trades` FastAPI route endpoint using a controlled provider, then executed its actual Dataset response model:

```
requested: category=spot, symbol=ETHUSDT, start_ms=0, end_ms=60000
returned:  category=linear, symbol=BTCUSDT, timestamp_ms=1700000000123
lossless serialization: succeeds
```

Repro saved work/e-heartbeat1/probe_review.py. This bypasses TestClient only to avoid the environment's known async hang; it invokes the published endpoint's exact sync function and response model, not a substitute implementation. It proves schema-valid wrong-symbol/out-of-range records can be served under the requested dataset. Bind endpoint resource/category/symbol/time bounds and relevant candle/observation interval to request, preserve requested-vs-covered bounds, and reject mismatches as provider_contract_error. Keep existing special OI-vs-funding guard. Add negative request-context fixtures, not only generic schema fixtures. Cursor/filter binding also remains provider-owned and needs explicit endpoint/adapter evidence.

`source` responses remain only object-shaped in OpenAPI and their rows are not validated by dataset_response(source), unlike market records. Scope/capability/source identity conventions need an explicit shared source contract before sources are used to authorize job capabilities. This is an integration limitation; no current malicious live provider was tested.

## Executed checks and limits

- Root venv now includes required dependencies (psutil and pytest-timeout installed by root after first attempt). Final safe targeted run: `../venv/Scripts/python.exe -m pytest tests/platform/test_canonical.py tests/platform/test_runtime_review.py tests/platform/test_scm_plan.py tests/platform/test_operations.py tests/platform/test_streaming_budget.py -q --basetemp=../pytest-e-heartbeat-final` → **24 passed, 2 skipped** (Windows symlink privilege and streaming benchmark needs disposable TEST_DATABASE_URL). Initial streaming import failed only missing psutil; this is superseded.
- `../venv/Scripts/python.exe scripts/check_contract_pin.py` → PASS9766c009.
- `../venv/Scripts/python.exe probe_review.py` → lossless True, wrong request scope accepted, NaN and metadata promotions rejected.
- `../venv/Scripts/python.exe -m src.api.scm_plan install --repo .` → valid DRY_RUN JSON, physical_changes=false; no SCM/ACL/DB modifications.
- API/TestClient test attempt with workspace TEMP/TMP still hung before output; terminated after bounded attempt. No independent47-pass claim. SCM tests/safe unit tests do not certify reboot, real installed-service account access, hard-kill recovery, actual exchange throughput or CPU/network fairness budgets. Ten-symbol benchmark is explicitly synthetic; no database was provided in this review.

## Exact follow-up commands for root

From immutable work/e-heartbeat1 with root full-access execution:

```
../venv/Scripts/python.exe -m pytest tests/platform/test_api.py tests/platform/test_http_contract.py -q --timeout=10 --timeout-method=thread --basetemp=../pytest-e-heartbeat-http-root
```

For PostgreSQL tests, set TEST_DATABASE_URL to a fresh disposable tester_acceptance/tester_ci database, then run test_jobs.py, test_admission.py and test_streaming_budget.py with a bounded timeout. These tests migrate and truncate tester_platform.jobs; use only that dedicated disposable database. Suggested command:

```
../venv/Scripts/python.exe -m pytest tests/platform/test_jobs.py tests/platform/test_admission.py tests/platform/test_streaming_budget.py -q --timeout=30 --timeout-method=thread --basetemp=../pytest-e-heartbeat-pg-root
```

Root should record actual HTTP/PG outcomes separately. Optional physical-service gates remain pending; this review has not authorized or performed installation.
