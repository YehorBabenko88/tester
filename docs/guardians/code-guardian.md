# Code Guardian P0 — #84 / Draft PR #90

Status: pure deterministic Python decision body only. Not deployed. There is no shell, database access, service stop/restart, LLM, network client, active-code mutation or live trading. Default disabled.

## Boundaries

Ownership: `src/api/guardians/code_guardian.py`, `tests/platform/test_code_guardian.py`, this document. Three distinct SCM, HTTP health and worker heartbeat probes must all fail within a 30-second freshness window. Trusted OS identity must attest exact service name, 40-character source HEAD, SID, binary image, configuration and clear lease. A missing condition always produces ALERT. A verified triple failure only emits `PROPOSE_OWNED_RESTART`, with an evidence hash. At most two proposals per hour are accepted based on the caller's budget reading; the production supervisor must persist and independently recheck the budget, lease and circuit breaker. Repeated fingerprints dedupe in memory, not yet across restarts.

After an authorized external recovery attempt, an independent verifier must see three distinct healthy probes and ownership again; executor claims alone do not grant recovery. Incident review requests accept only an error type and sanitized SHA-256 evidence, requesting isolated tests and Draft PR, not hot-patching active files.

## MUE-X source assessment

`mue/evo/swarm/orchestrator.py` provides useful typed roles and task messages. `mue/evo/autonomy/auto_correction.py` provides ideas for incident fingerprints and verified learning. `mue/evo/memory/lattice.py` suggests bounded persistence for sanitized evidence. We deliberately reject direct adoption of `mue/evo/security/guard.py` command string blacklists and `mue/evo/dna/sandbox.py` as a trusted code sandbox. A timeout-limited subprocess is not OS isolation.

## Tests on Windows ADMINIT (disposable worktree)

Run at repository root:

~~~powershell
python -m unittest discover -s tests/platform -p test_code_guardian.py -v
python -m compileall -q src/api/guardians/code_guardian.py tests/platform/test_code_guardian.py
python -m ruff check src/api/guardians/code_guardian.py tests/platform/test_code_guardian.py
python -m ruff format --check src/api/guardians/code_guardian.py tests/platform/test_code_guardian.py
~~~

These tests use mock evidence, not Windows services or PostgreSQL; they do not modify external resources. Keep `GUARDIANS_ENABLED=false`.

## Outstanding for MASTER #88

Implement trusted SCM/HTTP/heartbeat read-only probes, durable append-only incident receipts and restart budget, Windows own-service-only executor with a fenced lease, cross-reboot circuit breaker, physical ADMINIT failure injection/restart/rollback and 24-hour soak, exact-head CI and independent review. Code Guardian is **NO_GO for actual automated restarts** until those gates pass. Do not access PostgreSQL 5432 or merge/deploy automatically.

## Fault-injection and trust boundary (2026-11-18)

The independent `TestCodeGuardianFaultInjection` cases inject forged process claims,
mismatched/invalid source SHA, contradictory healthy observations, stale/future/malformed
samples, repeated fingerprints with rotated sample IDs, and failed after-checks.
They are pure synthetic unit tests: **no operating-system attestation is performed**.

Trusted evidence **must originate from separate read-only adapters**: Windows SCM
for service identity/SID/image/config, a pinned deployment manifest for expected
source HEAD, independently authenticated HTTP health, and a worker heartbeat
with lease identity. The current `Identity` and `Probe` objects are caller
assertions, **not cryptographic attestations**. A malicious caller can set all
boolean verification flags to true. Consequently the decision engine must not
be connected to a privileged executor until MASTER provides authenticated,
independent adapters and persisted incident/budget/lease receipts.

In-memory deduplication is not durable across process restart. A healthy after-check
does not prove that an external recovery was executed, only that fresh health
observations were supplied. A trusted external verifier and correlation to a
fenced execution receipt remain required. No automatic restarts or deployments.

## Evidence replay hardening

The three named adapters must not reuse the same SHA-256 evidence digest, even
when their source labels and sample IDs differ. Both proposal and after-check
paths reject duplicate digests; the negative tests inject this replay explicitly.
Digest uniqueness is a necessary sanity check, **not** proof that three adapters
are authentic or independent: a forged caller can generate three arbitrary
digests. Authenticated adapter provenance remains a MASTER-owned integration
blocker. No restart permission follows from these tests.
