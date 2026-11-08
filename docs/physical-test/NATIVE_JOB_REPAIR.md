# Native Job Object failure and component retest

The first regular-user foreground probe failed before spawning the supervisor:
installed pywin32 312 rejects `win32job.CreateJobObject(None, None)` with
`TypeError: None is not a valid string in this context`. The older broad mock
accepted every argument and missed the real binding restriction. The primary
[pywin32 binding](https://github.com/mhammond/pywin32/blob/main/win32/src/win32job.i)
declares a Unicode name; this installation's actual call is the reproduction.

The regression now models the strict string parameter and was RED before the
one-line change to an empty string. Root then ran platform135PASS/14SKIP/8subtests,
Ruff lint/format, unchanged contract/policy pins and OpenAPI equality. A real native
probe created two empty-name jobs, set kill-on-close on one and verified the other
was unaffected. No global object name, service, ACL, elevated process or security
setting was created or changed.

The repaired host class was loaded from the current worktree ONLY for the retest;
its child supervisor/API/worker came from unchanged frozen0ff59c69. The private
venv and actual limited-role PostgreSQL55469 were used. This is deliberately a
mixed component test, not a rebuilt immutable candidate or SCM service test.

Actual health:503, database/worker/storage=ok, provider=unavailable, instance_id=null.
Actual installed Chrome used a new retained tester-owned profile, real HTTP routes
and no mocks. It displayed `503 provider_unavailable`, performed a real retry and
market change, disabled downloads without a selection/provider, and had no uncaught
browser exceptions. Screenshot inspected: terminal-unavailable.png. All chart and
Strategy workspace panels stayed explicitly unavailable; no market data or
successful simulation was fabricated. This does not accept financial charts/F/G/TI.

The foreground supervisor exited0 after the stop marker; the kill-on-close job was
closed and the owned PostgreSQL stopped. Initial and retest reports plus executable
probe sources are retained beside this document and under the owned runtime root.

Original release0ff59c69 and manifest are immutable and retained, but the candidate
is RETIRED/NOT_INSTALLABLE because its native host contains this defect. The old
UAC wrapper is retained under backups; the current local and versioned wrapper
fail immediately instead of installing the retired SHA. Build and externally
verify a new immutable candidate before preparing a replacement wrapper. No UAC
retry is authorized by this code repair; the original cancellation remains binding.
