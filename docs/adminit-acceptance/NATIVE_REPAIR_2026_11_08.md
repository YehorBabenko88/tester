# Native attempt and repaired candidate

Current deployed portable source: `a3138eb9813832e9a839a99e54547001343ab3fd`.
Tree: `f2814cd8a12a7ea6f119bfad37a3e40a4e39bc67`.
Deployment payload SHA256:
`21b2854d02ac1d27563cd263e89504a13d4a6eb1724d3681588e63803eea506c`.

The user explicitly approved one b965 UAC invocation. It ran once and failed at
INSTALL before SCM registration: recursive icacls stripped effective file ACEs,
leaving empty DACLs and preventing private Python execution. No UUID services
were created. The failed acceptance report is retained as first-uac-failed.json.
It is not a cancellation, a native PASS, or authorization for another invocation.

All repairs were published before host execution. Actual Windows scratch tests
reproduced empty-file DACLs, locked-directory enumeration, inherited read-only
staging directories and unreadable newly-created PostgreSQL PID files. Fixes use
explicit file/directory descriptors, journaled owner-only recovery, no automatic
propagation to unseen children, mutable inheritance for future runtime files,
and verified same-volume atomic release/marker staging outside protected trees.
Six ACL tests and three staging/inheritance cases run on Windows, not mock PASS.
The complete source gate passed377 / skipped84 / subtests57; repeated fixed-temp
execution now works because only owned scratch fixture permissions are restored.

The ordinary individual owner restored only this newly created lab's ACLs through
published code, with original SDDL flushed to local receipts before mutation. No
elevation, owner change, global security change, byte modification or SCM mutation.
Two elevated-owned/unreadable runtime files were skipped. Raw SDDL journals stay
local. All inventoried old source/runtime bytes remain unchanged. An incomplete
98e089c directory is retained unregistered and was never executed or overwritten.

The isolated server was stopped safely after its binary, control-file path/PID
and recorded cluster identifier were checked. Only owned PG55470 was controlled;
PG5432 remains running. The repaired a313 candidate was staged and activated from
published Git bytes, then PG/API/worker restarted without administrative rights.

Exact a313 portable smoke passed:30 fresh closed BTC candles, limited-role storage
DDL/DELETE and visibility-access denial, keyring own-entry roundtrip/removal, and
ten actual instruments×five canonical candles with UNKNOWN coverage. The latest
retained backup/restore matched28 storage/lab tables and155 distinct raw records.
Earlier b965 Chrome evidence shows real UI download and candle/volume rendering;
frontend source bytes are unchanged. These are PORTABLE checks, not SCM or stress
acceptance. Source CI run42474942463 remains QUEUED; no release GO.

## Proposed renewed UAC request

Requires a NEW explicit user confirmation. After confirmation, gracefully stop
only portable tester, take a fresh owned backup, stop PG55470, verify all pins,
then request normal Windows RunAs once, with no policy bypass:

```powershell
& 'C:\TesterLab\releases\a3138eb9813832e9a839a99e54547001343ab3fd\scripts\windows\Invoke-TesterLabAcceptance.ps1' -ManifestSHA256 '21b2854d02ac1d27563cd263e89504a13d4a6eb1724d3681588e63803eea506c'
```

The invocation registers only the same UUID-bound LocalService pair, checks
start/health → stop → start/health, records source/instance/PID and leaves the
services running on success. Script SHA256 remains
`b81c06a38818cbaeb86a51e5bbc18d2b96947704f654b355f3697ecffd4e4143`;
the changed Manager/helper bytes are bound by the NEW deployment manifest above.
Any failure/cancellation is retained without automatic UAC retry.

Still NOT_RUN: actual SCM start/stop/start, native uninstall/crash qualification,
service-account history/browser acceptance. F/G/H and derived financial provider
remain NOT_INTEGRATED; historical visibility remains UNAVAILABLE/null.
