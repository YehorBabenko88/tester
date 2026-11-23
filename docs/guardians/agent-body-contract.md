# Tester Guardian Agent Body Contract P0

Status: DESIGN + pure policy/supervisor prototype; not deployed. Source research:
- https://github.com/KorroAi/mue-x/tree/main/mue/evo/swarm
- https://github.com/KorroAi/mue-x/tree/main/mue/evo/autonomy
- https://github.com/KorroAi/mue-x/tree/main/mue/evo/memory
- https://github.com/KorroAi/mue-x/tree/main/mue/evo/security
- https://github.com/KorroAi/mue-x/tree/main/mue/evo/dna

## MUE-X assessment from source

- `swarm/orchestrator.py`: `SwarmTask`, `SwarmMessage`, role delegation and in-process workers. Its coder role extracts a pre-existing memory snippet; this is not a proven autonomous production patch/release agent. Adopt typed task/event concepts, not the implementation.
- `autonomy/auto_correction.py`: SQLite error fingerprints and fix outcomes. Adopt a verified-incident memory model, but prevent learning from unconfirmed recoveries.
- `memory/lattice.py`: SQLite FTS5 layered memory. Use sanitized evidence identifiers and bounded per-scope historical receipts; do not store exchange credentials or raw secrets in agent memory.
- `security/guard.py`: audit and string blacklists. A substring blacklist is insufficient for Windows PowerShell, SQL and OS capabilities. Reject this security model; enforce OS identity/capability allowlists, constrained API calls and independent validation instead.
- `dna/sandbox.py`: subprocess with timeout is NOT a hardened OS sandbox against malicious generated code. Require isolated disposable worker/worktree, restricted token, no DB write credentials, no real network to trading endpoints and resource limits for any autonomous patch tests.
- `evolution/loop.py`: automatic AST mutation/rewrite/rollback. Keep mutation suggestions *offline* and Draft PR only; never mutate running Tester sources, data schemas or strategies automatically.

## Each agent has a real body (not merely a prompt)

1. **Identity** — immutable guardian_id, component, version, owner, source SHA, process/service SID; no shared privileged administrator identity.
2. **Senses** — typed, timestamped and source-attributed probes; raw telemetry preserved outside model prompts; optional redacted logs.
3. **Memory** — append-only incident ledger (sha256 fingerprint, scope, first_seen, last_seen, attempts, observed after-check, unresolved reason), bounded retention, independent evidence links; never falsely learn success.
4. **Goals** — explicit health/data integrity objectives, time budgets, service SLOs, circuit-breaker states, no subjective optimization of trading strategy by operational guardians.
5. **Brain** — pure deterministic policy gate first; optional analysis model second. Unknown, stale, contradictory or incomplete observations yield ALERT/NO_GO.
6. **Hands** — typed capability-specific adapter, no arbitrary shell/SQL. Code Guardian: PR/issue only + owner-scoped restart proposals. Data Guardian: existing queue/backfill API only. Storage Guardian: scoped existing cleanup helper only. Exchange Guardian: bounded official read-only HTTP via transport.
7. **Verifier** — separate read-after-write/restart probe and exact-version identity, never trust executor's own success claim.
8. **Communication** — bounded message bus with event_id, scope_key, evidence digest, lease epoch, idempotency key, status, timeout and signed/linked receipt; one writer per scope.
9. **Supervisor** — starts/stops agents, kill switch, least privilege, max concurrent repairs, budgets, delayed retry, crash and restart isolation, fixed policies and evidence audit.
10. **Skill importer** — references to audited source patterns allowed; no autonomous remote code import, auto-install, executable tool creation, uncontrolled dependency update or GitHub repo absorption in the runtime.

## State machine

`DISABLED -> MONITORING -> SUSPECT -> EVIDENCE_CONFIRMED -> PROPOSAL -> AUTHORIZED -> EXECUTED -> INDEPENDENTLY_VERIFIED`

Negative paths `SUSPECT -> INSUFFICIENT_EVIDENCE -> ALERT`; `EXECUTED -> VERIFY_FAILED -> CIRCUIT_OPEN -> ESCALATE`. Initial implementation only produces PROPOSAL and uses no executor; never claim any of the later states.

## Minimum evidence and schema

```json
{
  "schema_version": "guardian-event/v1",
  "component": "exchange",
  "signal": "http_429",
  "environment": "tester_preview",
  "scope": "bybit/rest/linear",
  "evidence_ref": "sha256:<64 hex digits>",
  "ownership_verified": true,
  "retry_after_seconds": 30
}
```

Model output cannot promote a state or write a health flag. Only strict, pre-reviewed policy recognizes events and emits action proposals. A separate executor must check capabilities and current observed state again, take an exact lease, execute via typed API, and collect an independent receipt.

## Runtime deployment

- Windows: guardian supervisor in a distinct unprivileged Windows service, with independently restartable functional workers and durable incident queue. Start disabled. Never use the interactive user account as service owner.
- PostgreSQL: isolation by dedicated role and Preview database/port, no access to 5432; read-only monitoring credential distinct from writable backfill queue credential. Original data immutable.
- Network: outbound Bybit public official HTTPS only for Exchange/Data; no SSH commands to unrelated nodes, and no exchange account trade permissions.
- Development: branch-per-issue (#84-#87) and an integration reviewer (#88), exact-head CI, independent negative acceptance + physical ADMINIT fault injection. Require manual promotion of any code changes.

## Milestones

M0: pure policy and supervisor prototype with no I/O (PR #89).
M1: four independent read-only sensors and capability manifests (Draft PRs #90-#93).
M2: durable journal + inter-process heartbeats, kill switch, backpressure.
M3: fault simulation and validated remediation adapters in disposable Preview.
M4: 24-hour soak, service restart/stop, hard reboot, record-identity integrity, slow source, TLS failure and controlled database recovery.
M5: opt-in PR-generation model. Autonomous code deployments remain prohibited until a separately approved release policy and proven rollback.
