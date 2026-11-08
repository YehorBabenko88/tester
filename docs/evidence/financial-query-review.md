# Explicit financial request boundary evidence

Parent: 5fa20ce18545d6b44ea71ef6bbb8d27e761a577e. Proposal posted before implementation
on Issue8 comment6709881290; versioned request schema is separate from immutable
shared record1.0. No default derived adapter or native tick resolver enabled.

2026-11-07, local Windows/Python3.14, isolated disposable PostgreSQL18:
`python scripts/acceptance.py --integration --security` PASS, 155 passed,
2 skipped (Windows symlink privilege), 1 upstream Starlette TestClient deprecation.
Runtime preflight, unchanged shared contract/policy pins, lint/format, OpenAPI and
financial request schema drift, PostgreSQL jobs/migrations, real supervised
API+DB+worker readiness/graceful shutdown, Bandit and pip-audit PASS. Cluster stopped.
Log: financial-query-acceptance.log. No hosted CI/installer/SCM acceptance claim.

39 financial tests execute byte-pinned actual B normalization, MASTER projection
and C binding/response from coordinator19f306c through E HTTP. They exercise strict
inputs/no defaults, unsupported resources, missing ports, reserved tick selection,
frozen snapshots, exact lexical step/cutoff cursor binding, independent quality
availability and original nanosecond acquisition/event cutoff boundaries. Shared
quality remains synthetic UNKNOWN; this does not establish B read proof/full-range
or production historical completeness. E does not duplicate production C math.

Reviewer platform_review found no P1/P2 in static review. Executed checks caught
and corrected two test/schema defects before publication: source provenance has
an additional derived C entry; Pydantic initially omitted a decimal pattern from
the generated JSON schema, now enforced by independent jsonschema tests.

B dc545c0a bounded-read proof was reported during this task. Its per-call snapshot
and full-range/quality/tick requirements are documented as future integration
gates; it is not wired or certified by this E change. Draft PR9 only, no main merge,
physical deployment, external Gateway/TI or trading.
