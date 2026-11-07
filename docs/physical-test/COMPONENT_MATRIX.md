# Physical installation acceptance — Issue27

This is a staged test of this PC only. No component is accepted from mocked tests.

| Component | Current evidence | Physical acceptance gap / owner |
|---|---|---|
| A sources | Real official Bybit REST: ten instruments, five closed minute candles each, two workers/shared2req/s, original evidence retained | Full trade history, archive admission and service jobs not yet proved; A + MASTER |
| B PostgreSQL | Dedicated PG18 cluster/migrations/limited app role prepared; existing5432 untouched | Inherited-output-pipe hang fixed; guarded retained-cluster resume, B/E migrations and denied DDL/DELETE probes passed, stopped; retained50-row ten-instrument data backup/restore and original payload/time/provenance checks PASS; full-history/native-service gates pending; B + MASTER |
| C analytics | Existing independently tested module in composed source | No live financial provider bound to API; NOT_INTEGRATED in physical candidate; C + E |
| E API/service | Private venv native imports passed; native lifecycle stop race fixed with regressions; platform121PASS/14skips before new guards | Actual SCM registration/start/update/recovery not yet performed; separate administrator UAC required; E + MASTER |
| D terminal | Frontend source shipped; earlier fixture/static-route tests are not physical UI acceptance | Real installed UI/screenshots and data charts pending; D + MASTER |
| F Strategy Lab | Separate Draft19 source, previously reviewed offline tests | NOT_INTEGRATED in physical candidate; F |
| G Codex Control Plane | Separate Draft24 | NOT_INTEGRATED; canonical PostgreSQL correction still outstanding; G via GitHub |
| Trader Intelligence | Inert source registry; no approved complete identified-trader feed | SOURCE_UNAVAILABLE / NOT_INTEGRATED; no collection or anonymous-trade attribution; H via GitHub |
| Resources/backup | Dedicated cluster resource settings; durable scoped journal; immutable release preparation | Native process limits, retained-data backup restore, crash/reboot/upgrade and disk/network measurements pending; E/B/MASTER |

All remaining stages retain data, logs, backups and old releases. No main merge or trading. SCM RUNNING, when established, will mean operational DB/worker/storage readiness; unsupported financial resources must remain explicitly unavailable.
