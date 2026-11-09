# Numeric-domain regression evidence (2026-11-09)

Base: PR46 `94868a60a600831c5773153de058fcf909c6ba8d`.
Isolated branch: `fix/nov9-catalog-numeric-domain`.

Root cause: `_number` qualified any finite Decimal string, so negative lastPrice,
volume24h, turnover24h and openInterest became OBSERVED screener values even though
the history collector already excludes negative price/OI. Normalization now rejects
only negative values of those four unsigned metrics: value=null, state=UNAVAILABLE,
reason=negative_value, raw_value retains the bounded original string with unchanged
source URL/time and observation time. Price change stays signed. Existing zero,
negative-zero and positive semantics remain unchanged. Funding code is untouched.

Pure/mocked commands executed in isolated work/nov9/catalog-fix with work/venv:

```
../../venv/Scripts/python.exe -m pytest -q tests/platform/test_catalog_numeric_domains.py --basetemp=../pytest-numeric-red
```

RED before source fix: 4 failed, 19 passed. All four failures were negative unsigned
metrics unexpectedly OBSERVED. No fixture/network/dependency failure.

```
../../venv/Scripts/python.exe -m pytest -q tests/platform/test_catalog_numeric_domains.py tests/platform/test_market_catalog.py tests/platform/test_bybit_catalog.py tests/platform/test_catalog_independent_sources.py tests/platform/test_catalog_history.py tests/platform/test_catalog_runtime.py tests/platform/test_runtime_identity.py tests/integration/test_catalog_market.py -k "not postgres" --basetemp=../pytest-numeric-green
```

GREEN: 133 passed, 6 deselected, 1 FastAPI/Starlette dependency deprecation warning,
3.13 seconds. Includes negative/raw-evidence, zero/positive, signed change and
nonfinite/malformed regressions plus existing source, pagination, history, scheduler
and adapter tests. No real PostgreSQL, host API/server, SCM, browser, office or
Bybit network execution. No ADMINIT configuration or secrets accessed. This is a
code regression result, not deployment, real source, or database acceptance.

Independent review follow-up: old persisted snapshots also require read-time
qualification before any new successful acquisition. Added eight cases covering
all four unsigned fields in legacy OBSERVED snapshots and retained STALE snapshots
after actual mocked ticker failure. RED on initial PR54: 8 failed / 23 passed.
`query` now applies the same negative-domain helper to its existing deepcopy;
response is unavailable with original negative raw_value/source/time, while the
stored OBSERVED/STALE observation and snapshot are unchanged. Signed changes and
zero remain unchanged.

Same full selected command with `--basetemp=../pytest-legacy-green`: 141 passed,
6 PostgreSQL deselected, one dependency deprecation warning, 3.17 seconds.
`git diff --check` passes. No real PG/host/runtime actions performed.
