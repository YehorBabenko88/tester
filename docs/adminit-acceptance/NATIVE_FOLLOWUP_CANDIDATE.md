# Native follow-up candidate

Download state, record verification and historical coverage are independent. Job.checkpoint.telemetry schema1 contains measured decoded HTTP response-body bytes, per-job unique acknowledged/verified record hashes, monotonic elapsed time and mean throughput. Original bodies are retained by SHA256 in the acquisition directory. Counters publish atomically with B writes after rereading stored values and canonical provenance; replay merges stable record identities. Bytes from an attempt interrupted before durable checkpoint publication remain unacknowledged/unknown, rather than invented. Received bytes include metadata/retries, not wire/TCP bytes or newly inserted raw rows.

Only work_complete after all acknowledged batches/record checks permits100percent; dataset coverage remains UNKNOWN. Legacy checkpoints have unknown counters and indeterminate progress. No expected bytes/record denominator is invented.

/api/lab/identity is a read-only operational endpoint excluded from the approved market OpenAPI contract. It binds executing source module, source/tree/deployment hash, installation and live instance report. It explicitly says host_reported and not_independently_verified for SCM; separate external SCM/file checks are required.

Calendar week/month jobs are bounded to93days and use a single calendar-aware acquisition window; other intervals remain one-day bounded. Read-only chart requests never ingest data or establish historical completeness.

Upgrade and worker-recovery scripts require separate user-approved elevation. Upgrade stops only app, retains whole oldruntime+metadata and verifies ownedPGpid; backup paths refuse junctions. Worker test can terminate at most one held-handle verified owned worker, then requires samejob secondlease/recovery, exact1440record checks and replay uniqueness. These are source-tested proposals until physical reports exist.

Low-rate soak defaults30minutes,10simultaneously submitted five-minute-window jobs perminute; actual worker concurrency is observed, not assumed. It samples own process resources, dedicated PG counters/WAL and disk; absent metrics cannot produce fullPASS. This is not saturation stress.

Validation: CPython3.14 acceptance414PASS/85SKIP/57subtests; CPython3.12 focused23PASS/1SKIP; frontend20unit +27browser tests PASS. Approved OpenAPI checks unchanged. Static independent upgrade/recovery reviews found path-write P2s; fixes add last-moment plain destination checks. Native physical acceptance is pending installation of a new published SHA.
