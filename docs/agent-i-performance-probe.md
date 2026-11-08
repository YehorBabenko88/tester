# Agent I bounded ingestion observation probe (#31)

Run `scripts/lab_intensive.py` only from a published source checkout after MASTER has installed and verified the exact candidate SHA. MASTER exclusively coordinates ADMINIT execution. This change was developed with synthetic boundaries, without installed-app or database load.

```powershell
python scripts/lab_intensive.py --expected-sha <installed-source-SHA40> --seconds 600 --out C:\path\outside-lab\intensive.json
```

The output must be absolute, outside the lab, in an existing directory, and absent on first use. Existing output is never overwritten at preflight. The harness inherits `lab_soak` source/manifest/instance binding and localhost API/PG55470 checks. It never installs, elevates, cancels, kills, retries admissions, or contacts PG5432. It emits exception types rather than exception text; DSNs and process command lines are excluded from reports.

At most ten public linear candle jobs are admitted per 15-second slot, over ten symbols, with at most forty slots and 400 jobs. Missed slots are not replayed. Admissions pause when the observed queue reaches twenty, stop above forty, and stop for failed jobs, low free disk, or sustained private-memory/read-latency limits. No admissions occur in the final thirty seconds. These are client controls, not enforcement of server resource quotas. Stop limits finish the probe without canceling already admitted jobs; pending IDs are retained for the coordinator.

Ten simultaneous client admissions do not prove ten concurrent workers. The report separately records observed running jobs and worker-process counts, plus per-symbol completion/read counts and observed queue-wait upper bounds. A running state missed between polls has unknown wait time. p50/p95 refer to observed client reads/admissions, not server processing. Job elapsed times are reported separately. CIM CPU is summed one-core percent; process I/O is not disk bandwidth. PostgreSQL WAL is cluster-wide. Process counters are scoped by configured service images, PID creation evidence and descendant ancestry; missing counters prevent an observed PASS. Instance/source identity is checked throughout.

`INTENSIVE_OBSERVED_PASS` requires completed jobs, no tracked pending jobs, nonempty verified reads for every symbol, and complete required metrics. It does **not** certify processing parallelism, saturation, retry, cancellation, CPU/WAL quotas, or Issue #31 as a whole. Existing source tests cover admission and job operations separately. Retry/cancel fault injection needs an independently disposable synthetic environment; this probe deliberately never exercises those mutations on ADMINIT.

Duration is an admission/sampling deadline, not a hard process deadline: in-flight socket, SQL and CIM operations finish before return. HTTP uses a 20-second socket timeout; slow streaming can extend a call. The coordinator must not present duration as a guaranteed wall-clock termination bound. No worker is killed to force termination.

Synthetic tests replace only network/DB/CIM boundaries, preserve report persistence, and verify a complete 60-second simulated run of thirty jobs over ten symbols with one observed worker. Real ADMINIT saturation and performance measurements remain unrun.

Historical failed jobs are preserved: an initial read-only repeatable-read snapshot records their IDs/count (maximum 10,000; larger inventories fail preflight). Each metrics, admission-pressure and final snapshot compares failed IDs against that baseline. Any newly observed failed ID stops the probe, including external jobs and an unchanged total count after another failure disappears. Sampled state cannot prove the absence of transient external failures that disappear entirely between observations. The harness never changes historical states.

For a separate, single read-only resource snapshot during a coordinator-owned load run:

```powershell
python scripts/lab_intensive.py --resources-only --expected-sha <installed-source-SHA40> --out C:\path\outside-lab\resource-snapshot.json
```

This mode binds the installed source/instance, samples CIM once and writes a new report. It performs no admissions or database probes, and does not convert missing metrics in another report into a PASS. The process sampler now uses five CIM calls independent of descendant count: two exact service queries, one bounded process inventory (10,000 maximum), and two perf-counter queries filtered to the owned trees (64 processes maximum per tree). Raw inventory images/command lines are never emitted. Missing creation evidence, child creation predating its parent, missing supervisor or partial counters fail closed. The ten-second CIM subprocess timeout remains enforced; latency improvement on ADMINIT still requires coordinator observation.

## Disposable PostgreSQL backpressure acceptance

`tests/platform/test_research_backpressure.py` exercises the actual canonical E FastAPI app through TestClient and the actual PostgreSQL JobQueue. It has no queue/DB doubles, market handler, network listener, exchange calls or service/process controls. Root alone coordinates the explicitly disposable cluster and runs the published source:

```powershell
# Set TEST_RESEARCH_DATABASE_URL privately to the coordinator-owned disposable DB.
python -m pytest tests/platform/test_research_backpressure.py -q
```

No DSN means the database test skips; there is no `TEST_DATABASE_URL` fallback. Before connecting, guards require a numeric loopback host, an explicit non-5432/non-55470 port, and database name `tester_research_*` with a nonempty suffix. Service, hostaddr and options overrides are refused; the selected numeric hostaddr is then pinned to override environment defaults. Connected database, server address and port are independently checked before migration or writes. A nonblocking advisory lock rejects overlapping cooperating runs. The queue must initially be idle. Only this test's acknowledged job IDs are deleted on cleanup; pre-existing rows remain unchanged and control settings are restored. No truncation occurs.

The bounded fixture uses max_pending=2 and max_running=1. Eight concurrent canonical API admissions must produce exactly two 202 responses and six 429 queue_full responses with Retry-After=5. Eight actual queue claims must produce one lease. Further rejected admission must leave every job column unchanged, including checkpoints, lease and timestamps. Completing one lease restores one admission slot. Draining returns 503 with Retry-After=5 and prevents another claim without changing existing jobs. A completed sentinel and any pre-existing idle history remain intact. This is queue/claim concurrency acceptance, not evidence of concurrent market processing or performance saturation.

Agent I performed no database connection while authoring this test. Actual PostgreSQL acceptance remains pending the coordinator's published-source run; passing DSN guard tests and a skipped integration test must not be reported as database acceptance.
