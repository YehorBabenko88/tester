# Nov9 pinned integration

Owner: MASTER. Scope: tester only, GitHub-first, Draft PR, no main merge.

## Host boundary

Readonly preflight identifies PC4, Windows 11 Pro build 26200, Ryzen 7 7800X3D (16 logical), 33,463,590,912 bytes RAM, 840,827,748,352 bytes free C:, KINGSTON SNV3S1000G NVMe. Current token nonadmin. Existing postgresql-x64-18 runs on 5432; database names AUTH_REQUIRED (passwordless readonly listing rejected). No tester SCM service; no lab listeners on 18765â€“18767 or 55469â€“55471. Prepared Nov7 releases are not installed services. C:/TesterLab absent. PC4 is not ADMINIT. No office connection, credential reuse, local installation, database mutation, server or service launch is authorized for this cycle.

## Pins and dependency matrix

|PR|Exact HEAD|Dependency / overlap|
|---|---|---|
|39|0ec0f9aebd67a811bd6254b8afbe38b5e0a742e9|Research F/J, G/K, ledger, charts; latest security equivalent already in 46|
|46|94868a60a600831c5773153de058fcf909c6ba8d|Unified catalog, scheduler, frontend; assembled baseline|
|47|fa2e7ce8cfeea2544fd2781fe1343fd941d122f5|Catalog source content included in 46|
|48|31eda083b51511e6672506fcb5de47d1abfb21e9|Persistent catalog/history content included in 46|
|49|dffbae52fdc1fc93d3409e1f8271e6408bf44388|UI contribution predates subsequent 51/52 fixes; no whole-file replacement|
|50|f87b393e0f0fdb6b2e6bb9298ea434cc934cded6|Harness snapshot predates assembled runtime; inspect patch, never overlay whole source|
|51|9290c2fdf69c818bae90a437df77b7b0bab3ba24|Identity/pagination changes included in 46|
|52|4018387264a597e3323ee32cb04edd1125528c69|Immediate search persistence included in 46|
|53|0891e253821db07b1912113fbdce90ddf1c315e7|Three-dot contribution benchmark/docs only; whole HEAD predates search fix|
|30|53a51dab006a2d5862c4285f8383b2a23d204f07|Older ADMINIT acceptance branch, stale failing checks; not current PC installation|

39/46 merge base a43d155a382e93f1c4ad09c55a4c6265eea00242. Exact clean three-way merge tree 06f03eb45263f50b2f4b438bfb7551ef4fcf9fb7 equals 46 tree. Preserve both parents in new candidate; do not reapply all overlapping component snapshots. HEADS_AND_CI.json records all 30 open drafts and check URLs.

## Execution gates and owners

1. MASTER: publish combined ancestry plus pins and exact-source manifest. No native payload activation.
2. A/E/B (review_ingest): numeric metric domain defect PR54; independent review found legacy stored snapshot bypass. Same PR must fix projection without deleting or rewriting raw facts. No acceptance until both acquisition and projection regressions pass.
3. MASTER + G/K: compose research ASGI before frontend catch-all, preserve child Host/Origin/auth and both lifecycle contexts. In-process tests first, hosted disposable PostgreSQL roundtrip next. No implicit migration, worker launch or default product activation.
4. D/L (review_ui_platform): global sorting, five-minute refresh, inline progress, selection persistence and chart navigation; verify actual browser on new exact candidate in hosted CI.
5. F/J + G/K (review_storage_analytics): single synthetic catalog/download/PG/chart/F/authG/result/canvas acknowledgement remains blocking. Existing G dispatch resolves an already published certified F bundle; it is not a strategy execution from arbitrary downloaded data. UNKNOWN coverage must reject F admission. Certified synthetic positive case must identify its separate proof.
6. H: authorized identified trader imports only. Public anonymous prints never identify traders. No permissionless collection. Trader Intelligence is NOT_READY, separate from market research.
7. MASTER: real Bybit only after coverage evidence; prior 46 real catalog/10-candle reports are producer evidence, not rerun on PC4 or new candidate. No real strategy acceptance from UNKNOWN coverage.

## Validation and limits

TDD composition RED: two missing-module failures. GREEN: two in-process route/lifecycle tests. Combined targeted local suite: 40 PASS, 4 PostgreSQL skipped. No local TCP listener or PostgreSQL process started. Add GitHub-hosted disposable PostgreSQL workflow on port 55472, exact PR HEAD checkout, genuine certified F / authenticated G transport / persistence / acknowledgement. Existing platform Windows, security and frontend jobs must run on the new SHA. CI green does not establish full combined browser E2E: missing bridge remains explicit until its own unmocked scenario passes.

## Checkpoint

Do not install or start a new Windows service on PC4 without separate confirmation. Automation remains PAUSED; no new schedule. Do not reuse ADMINIT paths or credentials. Next: resolve PR54 legacy cache domain defect, pin its reviewed SHA, run exact candidate CI, implement bounded combined synthetic browser harness and record manifest and actual test outputs. Status NO_GO until all mandatory evidence exists.


## Integration correction and independent review

PR57 edcd738 fixes render-qualified global sort. Initial combination with PR54 exposed three fixture failures because a supposedly valid price was negative; same PR corrected its fixture to0.5, preserving the domain guard. Root combined suite:200PASS,13SKIP,8subtests. PR56 bb69eb9 adds hosted genuine F publication→actual authenticated G job→PostgreSQL→same-job D canvas ACK and ledger check; market catalog deliberately disabled. This is a bounded gate, not full download/strategy E2E. Independent composition review found no blocking flaw; root added bounded server-thread shutdown assertion to real PostgreSQL HTTP test. New workflows retain exactsource manifests and private capability exclusion.
