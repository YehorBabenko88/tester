# PC4 experimental portable physical baseline

Installed source: `ea4d9b2cbfc15e1af54ea6bf73a530b68763873e`; tree: `4c3ae8380d74c0e4d55d614c6e1053a59ea0171c`. All 821 tracked source files were rehashed after the physical tests. No installed source was patched. This is **EXPERIMENTAL_PORTABLE / NO_GO for full acceptance**, with hosted CI still queued.

## Isolation

PC4, Windows 11 Pro 26200, standard user. A new private directory, venv, PostgreSQL cluster on 127.0.0.1:55471 and API on 127.0.0.1:18767 were created. The existing PostgreSQL service on 5432 was not changed. The runtime role has no elevated flags and does not own the database. The bootstrap owner retains its original OID but cannot log in; the operator uses a different private credential. No Windows Service, UAC, firewall, office network, ADMINIT, main merge, real trading or deletion was performed.

## Actual results

- Current-status Bybit catalog: **1995 linear and 76 inverse** instruments. Independent enumeration retained 11 original HTTP response receipts and terminal cursors. This does not certify historical listing coverage.
- **32** global sort cases passed across all catalog pages. Two scheduled refreshes were observed without a manual refresh request.
- One BTCUSDT download, then ten queued instruments, all completed. Including browser and timeframe cases, **25 jobs** completed. PostgreSQL contains **145 unique raw records**, **154 source links**, and no source orphans. All job acknowledgements match stored hashes; source envelopes were independently normalized again. The recorded job groups also verified 72 original HTTP body receipts.
- Actual Chromium with the real API, worker and PostgreSQL: **1 market E2E and 13 timeframe cases passed**. These cover real closed candles, volume, wheel zoom, pointer pan, crosshair, UTC calendar navigation, sorting, selection persistence and inline progress. Vivaldi also displayed the actual terminal. No API routes were mocked.
- Unsupported panels remain visibly UNAVAILABLE. Their 404/422/503 responses are **not** successful analytics acceptance. Real history remains coverage UNKNOWN and is not admitted to F.
- An actual `pg_dump` was restored into a **new** owned database. All 145 raw records and projections matched, including the immutable row checksum. Original databases and backup files remain retained.

## Resources and operational limits

The ten-job sample measured a peak sum of 313,769,984 RSS bytes (about 299 MiB) for the selected owned process tree, and 1.453125 CPU seconds over 13.07 seconds for surviving processes. Only one download ran at a time, as configured. The browser observed 1,326 local API responses with p95 128 ms, including intentional unavailable responses. These are bounded observations, not a stress benchmark or proof of hard JobObject limits. Recorded database size was about 15.5 MB and WAL files totaled 32 MiB.

The initial launch was correctly rejected by the strict argv verifier because the root argument used a different slash form. Its API, worker and PostgreSQL were stopped cooperatively and its data retained. The next launch used the exact native path and passed OS image, birth time, cwd, module and accepted TCP tuple checks.

## Next gates

The reviewed PR61/62/63 candidate adds a qualified synthetic PostgreSQL snapshot, authorized F execution, UI result binding and an explicit limited-role operator handoff. It must be published and tested as a new immutable installation. Full research, statistics/SL/TP, same-lab restart, crash recovery, native service and missing analytics remain unaccepted. Continue from CHECKPOINT.json; only the root agent may operate PC4 resources.

## Published research candidate attempt

Combined candidate `efd04cf77ded1e14f3b7787b9dba55d94bf9e78b` (tree `5a8ba22a4161e11326fb89374b39b44fa5683376`, archive SHA256 `8722660b242be7ba5ef61709cb5a9ffcc9605cbf1651289f83ef05f8d290da3f`, 902 files) failed physical prepare: the market canonical DSN supplies hostaddr, which the research DSN policy rejects. Market seed completed, research DDL did not. No sources were patched or preparation retried. The failed third lab and both earlier labs are stopped and retained. Existing PostgreSQL 5432 PID7584 remains unchanged. PR63 correction task: issuecomment-6747516363.

Independent exact-source local checks: 633 platform tests passed, 5 skipped, 30 integration deselected; domain archive checks gave 181 passes, 68 skips and two Git-object-dependent failures. All 14 metadata tests passed from the exact candidate Git worktree. These checks do not erase the physical preparation failure or substitute for queued hosted CI.

## Actual qualified research installation

Replacement `6a24e50d51f4d65783c4436b94fe87cc883acbeb`, tree `9488dc721a8b462ac346cbf845b55dee6505e840`, archive SHA256 `d16aedfeca91d91efff30667c625a09676adf9a6ba5540f7535f8b0ae9833112`: all 905 tracked files rehashed unchanged. The GitHub-first DSN correction from Draft65 was independently reviewed and combined Ruff passed. Fresh PostgreSQL prepare and limited-role handoff passed. An unexpected-schema CREATE grant caused actual refusal, then revoke restored the guard; the empty probe schema is retained.

Actual no-route-mock browser: qualified synthetic PostgreSQL snapshot → genuine F → durable execution → scoped G/E → D chart ACK → digest-bound summary, statistics, trade journal and entry-window SL/TP: **1 PASS (3.3s)**, three fills, one closed trade, funding `0.0100`. PostgreSQL independently confirms all three acknowledged fill IDs, one execution/artifact/chart job/ACK, summary replay and same-request reopen idempotency. Screenshot: screenshots/authenticated-synthetic-summary.png. Everything is visibly SYNTHETIC, simulated and real-market coverage NOT_PROVEN.

A new live 10-candle BTC job completed under the limited role; PG values match all acknowledged hashes. Its REAL/UNKNOWN qualified read remains unadmitted: both input preparation and registry reject it, leaving research table counts unchanged. Actual foreign Origin plus valid capability was 403; anonymous completed summary was 401. No external connection was made for the Origin header check.

A new candidate backup was restored into a new owned database, matching all compared raw/snapshot/artifact/execution/ACK content hashes. Data, private backups and failed environments are retained. A mostly idle post-E2E 11-second owned-process sample peaked at 420,544,512 RSS bytes; it is not a load or hard-limit test. Same-root restart and HiDPI remain separate active source tasks. Native SCM, crash/reboot, real historical admission and missing analytics remain unaccepted; hosted CI is queued.
