# Data Guardian P0 — read-only reconciliation

Issue #85 · Draft PR #91 · owner: Data Guardian only.

## Status and boundary

Pure Python proposal engine in `src/api/guardians/data_guardian.py`. It neither connects to PostgreSQL nor calls Bybit, CatalogHandler, SQL, shell, Codex, LLM, or the Windows service manager. Default `enabled=False`; deployment and execution are NOT implemented. It cannot attest the actual database, original raw bytes, or history completeness on its own.

A trusted, independently implemented read-only adapter must supply a bounded snapshot with explicit PostgreSQL schema and DSN isolation attestations (port 55432, database tester_preview, role guardian_readonly), bounded_verified history state and immutable baseline SHA256, original projection checks, source record SHA256 comparisons, source evidence digest, gap-proof digest and exact scope/window, lease-scope digest, provenance and closed-candle checks. These boolean attestations are **inputs, not cryptographic verification**; a future independent verifier must produce them and bind them to immutable evidence receipts. Untrusted callers must not be allowed to set them.

## Decisions

- `ALERT`: incomplete/unknown baseline, missing schema, unverified source, invalid scope, 5432, open candles, unsafe window, missing source gap proof or fencing.
- `PROPOSE_QUARANTINE`: duplicate timestamps, source mismatch, hash/projection conflict, invalid OHLCV including NaN, positive/negative Infinity and negative volume. No quarantine storage mutation.
- `PROPOSE_BOUNDED_BACKFILL`: verified missing timestamps, only after independently attested source gap proof bound to the exact identity/window, CatalogHandler identity, queue ownership, epoch lease and no overlapping writer. Proposal includes exact missing timestamps, bounded range, idempotency evidence digest, and `execution_authorized=False`. This is not an enqueue operation.
- `OBSERVED_CONSISTENT`: only the supplied bounded sample; never claim complete source history.

The proposal's bounding window may include existing candles between missing timestamps. A future executor MUST revalidate every missing identity and exclude already present keys before queueing, using a fenced transactional adapter; it must independently reread both raw and original projection afterward. No executor exists here. Boolean attestations, SHA strings, database names and ports are caller-supplied and can be forged; they are not proof of real PostgreSQL isolation or source authenticity. A trusted adapter must validate the actual connection and cryptographically bind independent receipts. Noncontiguous missing keys remain an explicit list; no range overwrite is authorized.

## Windows ADMINIT smoke (offline, no database access)

From a disposable checkout of the exact PR HEAD:

```powershell
$env:GUARDIANS_ENABLED = 'false'
py -m pytest tests/platform/test_data_guardian.py -q
py -m ruff check src/api/guardians/data_guardian.py tests/platform/test_data_guardian.py
py -m ruff format --check src/api/guardians/data_guardian.py tests/platform/test_data_guardian.py
git rev-parse HEAD
```

Do not configure PostgreSQL credentials or start any service for these tests. Check the GitHub Actions exact-HEAD URL separately. Never run against PostgreSQL 5432 or active Preview.

## Unimplemented acceptance gates

Independent PostgreSQL reader and exact original-byte SHA attestation; trusted catalog source proof; durable append-only incident ledger; independent lease/fencing executor; quarantined-range persistence; negative disposable DB tests; physical ADMINIT validation; exact-HEAD CI and 24-hour soak. Until these pass, **NO-GO for activation**, `GUARDIANS_ENABLED=false`.

MUE-X influence: typed role boundaries and incident fingerprints only. No dynamic code execution, shell sandbox, blacklist permissions or automatic mutation of active code.

## Independent negative matrix (P0)

Pytest covers nonfinite OHLC and volume, impossible candle bounds, duplicate identities, conflicting source/projection SHA, provenance mismatch, unknown history, missing source/gap proof, incorrect scope/window, mismatched DSN role/database/port, stale lease epoch, overlapping writers, and discontiguous missing keys. These are proposal-level checks only; no database integration is claimed.

Shared foundation Ruff I001 belongs to MASTER #88; this workstream must not edit foundation files. Exact-head GitHub Actions and physical ADMINIT acceptance remain required.
