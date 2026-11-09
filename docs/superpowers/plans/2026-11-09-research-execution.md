# Issue59 bounded execution bridge

Owner F/J/G/K. Base ea4d9b2. No physical actions.

Implement a separate opt-in server-resolved execution API. Operator registers an
immutable validated F DSL and a qualified snapshot loaded from the B repository,
then issues an expiring credential limited to that exact input digest. Existing
artifact-only grants cannot execute. REAL is rejected, including UNKNOWN; only
explicit admitted SYNTHETIC is supported by the current J publisher.

1. RED pure request/admission/binding tests; implement bounded canonical inputs
   and existing F/J calls without financial calculations.
2. Add explicit migration for immutable inputs, execution grants and leased jobs.
   Admission checks fresh auth/grant each time, actor/request replay conflicts,
   bounded active jobs, three attempts; stale leases cannot publish completion.
3. Worker executes actual F outside admission transaction, verifies/publishes J
   artifact, then atomically grants only that chart and commits completion.
   Publication can survive a crash before completion; deterministic replay resumes.
4. Add opt-in HTTP API with preserved existing Host/Origin rules and G methods.
   No routes/migrations/workers activated by default; typed chart Command returned
   for existing renderer and digest/fill-bound ACK.
5. Pure tests locally; PG transactional/restart cases hosted only. Publish Draft
   and explicit unsupported real/admission/UI limitations. No E2E claim before CI.
