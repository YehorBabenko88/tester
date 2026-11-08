# Fresh lab continuation — portable PASS, native approval pending

This report supersedes the initial BLOCKED_NOT_INSTALLABLE snapshot for the new
lab. It does not renew historical ProgramData evidence or claim release GO.

- SOURCE_SHA: `b96548e8a6f4b04505dd3e9f85fa52adfea8ac10`
- TREE_SHA: `a13c0af20044c5f091e182366cbafed054653a43`
- DEPLOY_PAYLOAD_SHA256: `09eb5a8816351a1f2d452707dad440a15af5856a81d306e6216a35e65a76bf6f`
- INSTALLER_TEST: source security regressions and actual published file lifecycle
  prepare/stage/rollback/remove PASS; all fixture files/backups retained. Native
  install/uninstall not executed.
- PORTABLE_SMOKE: PASS, real public Bybit job → A handoff → fenced B publication
  → API → Chrome candles/volume; 30 closed BTCUSDT minute candles. Additional ten
  actual instruments × five closed candles passed canonical API validation.
- SCM_APPROVAL: awaiting explicit user confirmation; no UAC invocation.
- SCM_STATUS: NOT_INSTALLED. Running portable supervisor is not an SCM service.
- PG_ISOLATION: new checksummed own cluster `C:\TesterLab\postgres-data` on
  `127.0.0.1:55470`; new limited runtime role. Existing PG5432/service untouched.
- BROWSER_SMOKE: real Chrome UI History Download job completed, actual candle and
  volume panels rendered; 2/12 panels contain records. Timeframe interaction was
  exercised, not comprehensive mouse/DPI acceptance. Remaining panels are visibly
  unavailable, not invented. Screenshot retained.
- CI_EXACT_HEAD: source run42472013289 QUEUED, not PASS.

The first published fresh candidate f7cc983 installed/prepared without elevation.
Its Bybit job failed TLS-chain validation in this environment. Original files and
acquisition journal were preserved. The repair was written/tested/published first,
then staged as a distinct immutable b96548e release after graceful portable stop,
owned-state backup/restore and PG stop. Requests now uses a checksum-bound private
bundle of existing Windows default server-auth CAs plus certifi. TLS verification
remains enabled; no global trust store, security policy or firewall change.

Post-load backup/restore into a new retained database matched all 28 storage/lab
base tables, including 78 distinct raw records, by row counts and content digests.
This excludes changing platform worker heartbeats and is not full-history proof.
Source, restore databases, dumps, old release and failure evidence remain intact.

The 35-wheel hash lock was installed offline into a private complete CPython3.14
runtime/venv. Native host imports and keyring's own random credential roundtrip
passed; the test credential was removed. Native imports left source and the
precompiled service runtime inventories unchanged. Python's executable signature
is Valid; pywin32's pythonservice.exe is NotSigned and pinned by its wheel hash.
Hashes are integrity receipts, not independent host attestation or signatures.

Source acceptance: 368 PASS / 84 SKIP / 57 subtests. Approved OpenAPI remained
unchanged and passed Python3.12/3.14 after the explicit422 description regression
repair. Independent review findings on conflict recording, timeout bounds, SQL
filter order, native startup bytecode and PG identity were corrected before use.

## One proposed UAC operation

After explicit confirmation, stop only the portable tester supervisor gracefully,
retain a fresh owned backup, stop only own PG55470, externally recheck release,
payload and runtime inventories, then invoke this published script once through
normal Windows RunAs. No policy bypass or persistent elevated agent:

```powershell
& 'C:\TesterLab\releases\b96548e8a6f4b04505dd3e9f85fa52adfea8ac10\scripts\windows\Invoke-TesterLabAcceptance.ps1' -ManifestSHA256 '09eb5a8816351a1f2d452707dad440a15af5856a81d306e6216a35e65a76bf6f'
```

Script SHA256: `b81c06a38818cbaeb86a51e5bbc18d2b96947704f654b355f3697ecffd4e4143`.

It scopes ACLs only under C:\TesterLab, registers the UUID-bound LocalService pair
TesterLabApp_89189096be4c4cb3bf2b07b44f25fd60 and
TesterLabPG_89189096be4c4cb3bf2b07b44f25fd60, tests start/health → stop → start/health,
records source/instance/service PID evidence and leaves services running on
success. It observes the existing PG5432 service before/after without changing it.
Cancellation or failure is retained and is never an automatic UAC retry.

REMAINING_BLOCKERS: native approval/SCM lifecycle; exact-source CI; native crash
recovery/uninstall; derived financial provider and F/G/H NOT_INTEGRATED;
historical visibility UNAVAILABLE/null; full-history/resource stress qualification.
