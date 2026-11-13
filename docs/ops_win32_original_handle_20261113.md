# Original Win32 handle adapter — separate hosted Windows acceptance

This opt-in extension remains **outside** `src.integration.pc4_runtime` and
does **not** modify the installed PC4 tester.

`scripts/ops_win32_handle.py` wraps the original process handle retained by
the exact `subprocess.Popen` object. It calls Windows `GetProcessId`,
`GetProcessTimes`, `WaitForSingleObject` and `GetExitCodeProcess` on
that **original handle only**. It never calls `OpenProcess` by a PID or
accepts a file receipt as a substitute for the handle. Process creation
time is independently rechecked in 100-ns FILETIME units before each
wait/poll.

A zero exit is accepted only when the original Win32 handle reports a
signaled process and exit status 0. Timeout, mismatched PID, changed
birth, failed kernel API, stale owner or missing handle refuse sealing.
The process-control path contains no forced termination or UAC.

`tests/platform/test_ops_native_windows_process.py` uses **real**
temporary Python child processes on a separate GitHub-hosted Windows
runner. It tests two sequential native handle/closure generations and
rejects a stale birth before creating stop files. This establishes
Windows original-handle behavior, **not** an installed PC4 native
service/portable restart.

The `windows-native-witness` job in `.github/workflows/ops-portable.yml`
runs separately from `portable-pure` and `portable-pg`. No Windows
native PASS may be stated until that exact-head job has completed
successfully with logs.

Still BLOCKED: generation-scoped stop integration into the actual PC4
API/worker, durable native startup reservation, independent source
attestation, owned PG incarnation and checkpoint/recovery across
controller crash, and PC4 physical E2E. Existing PC4 process records
must never be removed to bypass a refusal. Release remains NO_GO.
