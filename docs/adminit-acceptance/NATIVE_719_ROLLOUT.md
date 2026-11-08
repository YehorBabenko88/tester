# Native reader rollout, 8 November 2026

Status: reader upgrade PASS; integration release NO_GO.

One explicitly approved UAC ran published Update-TesterLabRelease.ps1 at source 719b02eea4134730b5d688143022706a9cae8dbe, upgrade manifest SHA256 cc295c8cde47af6267f0b88e76a2e3108574820929e1127d2687ff527439a970. No worker crash was requested or executed. The previously cancelled recovery UAC was not retried.

The script reports OWNED_BACKUP_RESTORE_PASS for 28 tables and UPGRADE_PASS, committed, no rollback required. Prior runtime/metadata retained in the reported rollback bundle. Both PostgreSQL services remained running with their earlier PIDs: owned 55470 PID5132, preexisting 5432 PID11740. App SCM Running PID6860 under LocalService; host API instance 8d7e04a83b07471d8282cdc02af51c46. Host-reported identity is not independent loaded-image attestation.

Full installed source and native inventory hashes verified. Source tree 4e5d8c426ce76aa00f61032125eb6284d7b089ea, payload a1572b25fffd82ea5a4650ba57e76b5c66026a24f85d7f7149467c7f719552d2, native receipt 004f9ed3b97f760e3936ac425d4d0d62e0cdcea61d8c5823dd95bbbdddefb317.

Post-upgrade 1000 historical candle values and data+coverage canonical hashes exactly match the pre-upgrade reference. Coverage remains UNKNOWN. API GET observations 10/100/1000 rows: 0.175/0.371/1.822 seconds. Isolated read-only profiled reader1000:3.209s peak working set72,265,728 bytes, versus prior legacy isolated profile1,272,455,168 bytes. These are individual observations, not service throughput or statistically controlled latency claims.

The published intensive harness8f69ced initially refused to start because it requires no historical failed jobs: dedicated DB has completed293, failed1, queue0. No admissions were made. Agent I is correcting baseline handling without deleting/retrying that historical job; new failures must still stop admissions.

Independent agents I/J/K/L were actually started in separate native worktrees and branches for Issues31/32/33/34. Runtime capacity allows root plus three active children, so launches overlapped but were staggered. Draft PR35 performance, PR36 UI and PR37 strategy evidence are published; K is implementing the control seam. None may modify ADMINIT or main. Source integration is not implied by Draft PR publication.

Mandatory gates still incomplete: authentic coverage/admission (UNKNOWN and empty refs rejected by F), canonical F/G/H integration and rendered simulated fills, crash/restart after the cancelled request, physical mouse testing, GitHub CI execution and full load assessment. CI snapshot shows queued hosted jobs without assigned runners, not a proven billing/account root cause. No reruns or settings mutations were made. Real trading is disabled.
