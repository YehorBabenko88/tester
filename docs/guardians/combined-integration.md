# Combined Guardian proposal integration

This candidate composes the frozen MASTER and four pinned Guardian modules. It
provides a proposal integration API, not a running supervisor service or an
installed runtime. No child-owned implementation or test is replaced.

## Single authority and explicit gates

`src.api.guardians.integration.ProposalBridge(supervisor, enabled=False)` wraps a
caller-owned `DurableSupervisor`. Both gates must be enabled explicitly; the
supervisor still defaults to `GUARDIANS_ENABLED=false`. Every bridge call requires
`environment="tester_preview"` and a separate literal `ownership_verified=True`
attestation. `tester_lab` is rejected before submission, never relabeled.

These booleans are trust inputs supplied by an independently controlled caller;
they do not authenticate that caller or validate live service, filesystem,
database or signed package identity. No runtime automatically supplies them.

All methods return `Admission(receipt, reason)`. A rejected child result has no
receipt. MASTER may also return a durable DENIED receipt. Every Admission has
`execution_authorized=False`; `authorize_execution()` always raises
`PermissionError`. An accepted PROPOSED receipt is no permission to retry, restart,
write, download, quarantine, delete or claim recovery.

| Method | Input | Admitted proposal |
| --- | --- | --- |
| `code(result, *, environment, ownership_verified=False)` | CodeGuardian Result with restart event and matching evidence fingerprint | PROPOSE_OWNED_RESTART |
| `data(snapshot, finding, *, environment, ownership_verified=False)` | Data snapshot and Finding, revalidated by child `to_policy_event` | PROPOSE_BOUNDED_BACKFILL or PROPOSE_QUARANTINE |
| `exchange(observation, verdict, *, now, environment, ownership_verified=False)` | Exchange Observation and proposal Verdict | STOP_SOURCE, RATE_BACKOFF, BOUNDED_RETRY or MARK_UNAVAILABLE proposals |
| `storage(manifest, *, scope, min_free_bytes, environment, ownership_verified=False, disk_telemetry_verified=False)` | Hash-checked OBSERVED inventory, independently attested scope/disk measurement and threshold | PROPOSE_PAUSE_DOWNLOADS |

Code review metadata remains a manual review artifact rather than a fabricated
supervisor incident. Storage maps `PROPOSE_PAUSE_NEW_DOWNLOADS` to the foundation
low-disk event and prefixes the verified inventory digest with `sha256:`. Storage
cleanup and quota alerts remain advisory: inventory does not prove current file
age, safe ownership or atomic reparse protection and no cleanup event is built.
A manifest hash establishes integrity of supplied bytes, not trustworthy origin,
freshness or physical measurement. These require the independent caller.

## Durable scope correction

MASTER stores each admitted event scope in `receipts.scope` and includes it
in the hash-chained PROPOSED admission payload. `identities.scope` is a checked
lookup index: changing it makes chain verification fail. `reserve` binds its
requested scope to the scope in the authenticated admission receipt.

Windows Code restart service scopes are lowercased at admission, reservation and
finish using the original persisted role/action. `TesterAPI` and `TesterApi`
therefore share one lease and restart budget. Other Guardian scopes preserve
case. Evidence fingerprints remain unchanged. A caller cannot rotate resource
aliases or case variants to bypass restart budgets or same-resource leases.
Distinct valid services keep independent scopes.

A dedicated existing receipt database is upgraded by adding nullable scope
columns to receipts and identities. Historical identities remain deduplicated;
their unknown scope is never inferred, and a new reservation is refused even if
a mutable legacy index is populated. Existing legacy receipt hash chains retain
their original payload format; modern scoped receipts cannot downgrade to it by
setting scope to NULL. Active legacy leases with unauthenticated scopes block
opening the upgraded supervisor (`guardian_legacy_unbound_lease`), preventing an
old mixed-case lease from becoming invisible. Historical restart-attempt charges
are case-folded because that table belongs only to Code restarts. No legacy
admission is silently reauthorized. The local hash chain detects the tested
mutations; this is not an externally signed or remotely authenticated authority.

Legacy supervisor test fixtures reserved `api` after submitting
`tester-preview/api`. Those valid-path fixtures now use `tester-preview/api` for
reserve/finish and lease-expiry SQL. Their assertions were retained. Alias use is
now covered explicitly as a denial regression.

## Retry ownership and open release gates

`src/ingest/transport.py::Transport.get` remains the sole existing HTTP executor.
This integration adds no network or retry executor and does not call transport.
Its observed 403 retry, TLS retry, host-only allowlist and independent attempt
budget still conflict with Exchange Guardian safety policy. Combined proposal
acceptance does not certify that execution path. Transport-owner tests and fixes,
a shared execution budget and independently attested installed identity remain
release gates. No live execution can be authorized through this bridge.

The authenticated independent after-check, installed `tester_lab` identity,
physical Windows lifecycle and ADMINIT acceptance remain separate requirements.
`finish(verified=True)` still records an external assertion, not authenticated
proof. Download completion and historical coverage are not inferred here.

## Verification

Run from the combined repository with its pinned environment:

```powershell
.venv/Scripts/python.exe -m pytest -q tests/platform/test_guardian_combined_integration.py tests/platform/test_guardian_supervisor.py tests/platform/test_guardian_policy.py tests/platform/test_code_guardian.py tests/platform/test_data_guardian.py tests/platform/test_storage_guardian.py tests/platform/test_exchange_guardian.py
.venv/Scripts/python.exe -m ruff check src/api/guardians/integration.py src/api/guardians/supervisor.py tests/platform/test_guardian_combined_integration.py tests/platform/test_guardian_supervisor.py
.venv/Scripts/python.exe -m ruff format --check src/api/guardians/integration.py src/api/guardians/supervisor.py tests/platform/test_guardian_combined_integration.py tests/platform/test_guardian_supervisor.py
```

Scope regressions were first run against the unfixed supervisor: both failed
because alternate scopes were RESERVED. The fix made both pass. Combined bridge
tests then failed with the module absent before implementation. Negative input
tests additionally demonstrated malformed Storage proposal lists and Exchange
time were not rejected; validation was added and those tests passed.

The suite covers all four child-to-MASTER proposal paths; default-off child,
bridge and MASTER gates; every adapter rejecting tester_lab; modified/stale
inputs; Storage telemetry attestation and cleanup denial; durable deduplication
and reopen; scope binding; restart accounting; lease exclusion; backpressure;
unverified finish; persistent kill; and legacy scope-less admission rejection; Windows service case aliases;
mutable scope-index tampering; hash-format downgrade; legacy active-lease
rejection; preserved historical restart charges; and malformed typed child results.
Tests use temporary SQLite and read-only temporary inventory, not a live service,
Bybit or PostgreSQL instance.
