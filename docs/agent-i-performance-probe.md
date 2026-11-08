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
