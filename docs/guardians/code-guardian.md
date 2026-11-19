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

## Windows reinstall preparation — read-only gate (2026-11-19)

**Status: NO_GO / preparation only.** Code Guardian remains disabled and has no
installer, service controller, or privileged executor. This checklist is a
manual acceptance contract for MASTER's separate installer design, not an
authorization to operate on the running machine.

### Before any installation (read-only inventory)

1. Record the full 40-character candidate release HEAD, immutable artifact
   SHA-256 and signature/trust root; compare both against a reviewed release
   manifest. A branch name, unsigned zip or CI success on another HEAD is not
   sufficient.
2. Inventory *without changing state*: existing Tester service names,
   service SIDs, executable paths, binary hashes, start types, run-as accounts,
   current process IDs, service health and worker lease/heartbeat identities.
   Include Windows Event Log evidence and a snapshot of active configuration.
3. Inventory application and dedicated PostgreSQL ports, database identity,
   cluster path, roles, schemas, current backup status and recovery targets.
   The existing PostgreSQL **5432 must not be touched**. Record service and
   file ACLs, disk free space, junctions/reparse points and path ownership.
4. Record existing release path, active Preview URL, runtime data directories,
   migration level, pending jobs and persistent incident journal. Do not
   infer that an unresponsive service is safe to replace.
5. Prepare a *separate* candidate directory with an immutable manifest and
   least-privileged ACLs; do not overwrite the active executable, data, logs,
   PostgreSQL files or configuration. Keep `GUARDIANS_ENABLED=false`.
6. Prove that backups are restorable **in a disposable isolated environment**:
   capture hashes, consistent database snapshot/recovery point, config,
   service manifests and release artifacts. An untested backup is not a
   rollback plan. Do not test restoration against the live instance.

### Independent rollback acceptance (MASTER-owned execution gate)

- Preserve the previous executable, config, DB snapshot and migration
  metadata; document the exact previous service identity and binary SHA.
- A rollback must restore both code and schema/data compatibility. Never
  downgrade a database in place without an independently tested reversible
  migration or a verified snapshot restore in isolation.
- Prove in a disposable Windows sandbox that candidate failure leaves the
  original installation and database untouched. Verify source record counts,
  unique keys, provenance hashes, job leases and service identities before
  and after simulated failure.
- Require independent SCM + HTTP + worker probes with authenticated provenance
  and pinned version identity before any recovery may be called VERIFIED.
  The current caller-provided boolean flags cannot satisfy this gate.
- Refuse promotion on a missing backup, mismatched SHA/signature, unknown
  process ownership, stale/conflicting probes, journal corruption, exhausted
  restart budget, absent external rollback receipt, or CI not green on the
  exact candidate HEAD.
- Require MASTER's written release approval, successful exact-head CI,
  independent Windows physical acceptance, and a documented rollback drill
  before scheduling any cutover. No cutover or uninstall is authorized here.

### Code Guardian acceptance commands (disposable checkout only)

```powershell
python -m compileall -q src/api/guardians/code_guardian.py tests/platform/test_code_guardian.py
python -m unittest discover -s tests/platform -p test_code_guardian.py -v
python -m ruff check src/api/guardians/code_guardian.py tests/platform/test_code_guardian.py
python -m ruff format --check src/api/guardians/code_guardian.py tests/platform/test_code_guardian.py
```

Record exact commit, Python/Ruff versions, command exit codes, test
PASS/FAIL/SKIP and workflow URLs. Do not label these commands PASS until
they execute against the exact candidate commit. CI that fails in shared
foundation remains a blocker, even if isolated Code tests succeed.
