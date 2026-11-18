# Data Guardian P0 — read-only reconciliation

Issue #85 · Draft PR #91 · owner: Data Guardian only.

## Status and boundary

Pure Python proposal engine in `src/api/guardians/data_guardian.py`. It neither connects to PostgreSQL nor calls Bybit, CatalogHandler, SQL, shell, Codex, LLM, or the Windows service manager. Default `enabled=False`; deployment and execution are NOT implemented. It cannot attest the actual database, original raw bytes, or history completeness on its own.

A trusted, independently implemented read-only adapter must supply a bounded snapshot with explicit PostgreSQL schema and DSN isolation attestations, immutable baseline SHA256, original projection checks, source record SHA256 comparisons, provenance and closed-candle checks. These boolean attestations are **inputs, not cryptographic verification**; a future independent verifier must produce them and bind them to immutable evidence receipts. Untrusted callers must not be allowed to set them.

## Decisions

- `ALERT`: incomplete/unknown baseline, missing schema, unverified source, invalid scope, 5432, open candles, unsafe window, missing source gap proof or fencing.
- `PROPOSE_QUARANTINE`: duplicate timestamps, source mismatch, hash/projection conflict, invalid OHLCV. No quarantine storage mutation.
- `PROPOSE_BOUNDED_BACKFILL`: verified missing timestamps, only after independently attested source gap proof, CatalogHandler identity, queue ownership, epoch lease and no overlapping writer. Proposal includes exact missing timestamps, bounded range, idempotency evidence digest, and `execution_authorized=False`. This is not an enqueue operation.
- `OBSERVED_CONSISTENT`: only the supplied bounded sample; never claim complete source history.

The proposal's bounding window may include existing candles between missing timestamps. A future executor MUST revalidate every missing identity and exclude already present keys before queueing, using a fenced transactional adapter; it must independently reread both raw and original projection afterward. No executor exists here.

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
