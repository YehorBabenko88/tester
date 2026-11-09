# Hosted combined synthetic gate

Base: PR55 b02d5f1fe334d07302eacb9d8c1d1d3459e7f9b5. This test-only fixture
composes the ordinary market application and real persistent research application.
It never changes the product default, native runtime entrypoints, owner predicates,
Host/Origin middleware, authorization or startup migrations.

The explicit hosted prepare command migrates only the fixed disposable CI database,
executes the genuine pinned F fixture, publishes its verified bundle, and issues a
short-lived artifact-scoped credential. The real G HTTP client submits a stable
request, proves replay returns the same job and waits for real worker dispatch.
The browser submits that same request, checks the same job/digest, draws exact fills
and sends the D ACK. A separate ledger query requires that exact ACK/fill set.
The unauthenticated browser attempt must fail first. No API route mocks are used.

This is synthetic research with SIMULATED execution and real-market coverage
NOT PROVEN. The market provider is unavailable and catalog is disabled. It does
not demonstrate catalog acquisition, archive download, market ingestion, arbitrary
user strategy execution, native process ownership or trader-specific history.

Local verification (no TCP, PostgreSQL or browser launch): 7 pure tests passed,
Ruff passed, both JavaScript files passed node --check. Actual hosted integration
is UNVERIFIED until the new workflow executes successfully on the published SHA.
The workflow uploads exact HEAD/tree and tracked-file SHA256 manifest, sanitized
server logs, final ACK outcome, JUnit and screenshot. Private capabilities stay
in RUNNER_TEMP, excluded from uploads. No trace containing HTTP credentials is enabled.

Reproduce pure checks: `python -m pytest tests/platform/test_combined_browser_fixture.py -q`.
Hosted gate: `.github/workflows/combined-synthetic-browser.yml`; timeout 15 minutes.
Initial HEAD 146823a145b851caf6fb22020833504ef5310b38 had an actual Actions
validation failure before any jobs: run 42652212268
(https://github.com/YehorBabenko88/tester/actions/runs/42652212268).
Its job.env referenced runner.temp, which is unavailable in that context.
Follow-up moves the path export into a Bash step using RUNNER_TEMP/GITHUB_ENV
and removes the redundant GITHUB_ACTIONS override. This source correction is
not proof of successful Actions validation until a new run creates a job.
Each G readiness/dispatch phase shares a 30-second deadline; browser timeout is 60s.
App shutdown cancels only its in-process polling task. CI service/container cleanup
is performed by GitHub Actions; this fixture performs no database DROP or DELETE.
