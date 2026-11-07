# Query scope binding — PR9 comment 6708480531

Base: 6d8b1b97433fd850a8900ad8a3750b682319dc3a.
Actual HTTP reproduction returned canonical BTCUSDT/linear at 1700000000123 for a
spot/ETHUSDT request [0,60000). Standalone schema validation did not bind a request.
New adverse tests reproduced the failure before implementation.

The API now validates a response snapshot against its original request, independently
of the mutable private query copy passed to the provider. It binds market identity,
half-open timestamps, requested dataset/kind, interval, bar boundaries and coverage.
Coverage-envelope bounds must exactly match the request; row bounds stay inside it.
Standalone shared-v1 coverage/source-capability rows lack market identity fields, so
authentic evidence ownership still requires the scoped B resolver. No schema v1.0
file or C aggregation calculation changed.

E wraps provider page cursors in per-app HMAC tokens bound to resource and every query
filter. Cross-scope, tampered and stale-instance tokens fail422 before reader calls.
The raw provider cursor is recovered losslessly. Restarts invalidate page tokens, not
persisted job checkpoints. Provider source cursor max2048 characters; transport token
max4096 with explicit encoding-size checks. These rules are documented in api-contract.

Scope errors return502 provider_scope_mismatch with unavailable status and a bounded
provenance summary using source-kind/content hash/reference and run digests. No raw
data rows, source URLs, DSNs, credential strings or arbitrary reason text are echoed.
Valid responses retain their full canonical availability/provenance context.

Independent review found provider-query mutation could bypass the initial fix. An HTTP
provider mutation regression reproduced200, then passed with502 after using only the
original unexposed request as authority.

Final gate: `python scripts/acceptance.py --integration --security`, disposable local
PostgreSQL18/Python3.14, exit0: 68 PASS /1 Windows symlink privilege skip /1 upstream
TestClient httpx warning. Includes21 new scope cases, real API/worker/DB lifecycle,
pin-integrity, lint/format/OpenAPI, Bandit and pip-audit. PostgreSQL stopped cleanly.
No service installation, main merge, provider default change or physical deployment.
