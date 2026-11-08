# Native acceptance follow-up implementation plan

Goal: distinguish acknowledged download work from historical coverage, expose measurable restart-safe telemetry, upgrade immutable native installation, and gather bounded physical evidence without changing main or PG5432.

Architecture: retain PostgreSQL fenced checkpoint publication. Add versioned telemetry inside existing Job.checkpoint; validate committed raw/projection values before publishing record counters. Download completion is the job state plus work_complete; coverage remains independent UNKNOWN. Installed identity is read-only operational metadata with explicit host-reported provenance. Existing immutable releases retained.

Scope authorized by user PR30 continuation and comment6721233645. No design preference needed for the specified status correction. New product integrations remain preparatory interfaces/docs until genuine source/artifacts exist.

- [ ] Backend telemetry tests first: measured successful HTTP body bytes; cumulative acknowledged record counts, reread verification; resume replacement per logical batch, no double counting; unknown denominators.
- [ ] Frontend tests first: distinct badges, indeterminate missing denominator, no false99percent; legacy unknown values; contextual loopback notice only if application-owned.
- [ ] Read-only identity endpoint excludes approved market OpenAPI contract, verifies installed immutable metadata; mismatch503.
- [ ] Reviewed published immutable upgrade PS: exact old/new manifest identities, scoped backup, app stop, stage, nativePTH/source repin, restart, rollback onfailure. Separate single UAC approval before execution.
- [ ] Publish source commit + exact SHA/tree/artifact manifest in DraftPR30, run source acceptance and browser tests.
- [ ] Physical newSHA identity/API/counters/UI, all supported timeframes and mouse actions. Unsupported W/M remains explicit until validated official calendar support is wired.
- [ ] Ten-instrument measured soak: default30minutes, bounded one-day windows and public request rate; sample own processRAM/CPU, disk usage, dedicated PostgreSQL write statistics; retain report with actual duration and concurrency.
- [ ] Scoped crash/restart recovery and replay uniqueness test in reviewed published harness; no privileged operation before distinct explicit UAC approval. No automatic elevation retries.
- [ ] Prepare F/G/H integration inventory/gates and simulation overlay contract; no invented simulation or real trades.
- [ ] Evidence report separates HOST_REPORTED / INDEPENDENTLY_REPRODUCED / CI / NOT_RUN and installed vs PR/source SHA. No releaseGO while required gates absent.
