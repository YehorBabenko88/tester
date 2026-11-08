# Active supervision and resumption

Current execution evidence supersedes historical setup wording below: actual system-origin invocations received2026-11-07T14:40:23.454Z and2026-11-07T15:27:23.699Z, with continuation tools executed in the existing worktree. This proves those invocations, not guaranteed future runs. Issue25 standalone/P0-first restrictions were saved through official automation_update and configuration read back. Current scope includes A–G and new Draft PRs; direct chat follow-up permission remains A–F only. Read docs/coordination/MASTER_STATUS.md and CHECKPOINT.json for exact current pins and next work. Trader Intelligence S1+ remains gated on P0 and permitted source evidence.

Repository scope: YehorBabenko88/tester only. Approved #8 and Issues #14–#17 remain authoritative. Ordinary cycle states: NEW, IN_PROGRESS, BLOCKED, READY_FOR_REVIEW, CHANGES_REQUESTED, ACCEPTED; execution state is separate (RUNNING_CONFIRMED, AWAITING_AGENT_EXECUTION, NEEDS_USER_INPUT).

## Installed continuation mechanism

Codex Desktop heartbeat `tester-master-coordination-cycle` is configured ACTIVE, every15 minutes, targeting the existing MASTER chat. Configuration was created/updated with the official automation_update tool and verified by readback. Current execution worktree is the explicitly named existing Git worktree `work/master-supervisor`, branch coordination/master. Native create_worktree call stalled without operationId or attachment; inspected artifacts/Git worktrees showed none, so a single Git fallback was created. Do not create a worktree for every heartbeat.

**Configured is not a verified invocation.** Until an actual scheduler-origin turn or run record is observed, keep AWAITING_FIRST_INVOCATION. This setup turn and manually executed regression tests do not count as an automated run. Do not claim continuous monitoring merely because status=ACTIVE. A heartbeat targeting an already active chat may have to wait for an available turn boundary; do not launch a duplicate standalone cron or external Windows scheduler to bypass it. Keep app/machine available for local tasks.

## Cycle procedure

1. Read CHECKPOINT.json, current MASTER_STATUS and latest GitHub #1–#17, PRs/reviews, A–F branches and Actions. Never rely solely on old reports. Record checked_at in UTC plus Europe/Berlin display time, exact producer/consumer HEADs and what changed.
2. Inspect real agent chat status via the app. User explicitly authorized follow-ups to existing A–F chats. Dispatch only idle agents with actionable work; do not interrupt active sessions or count GitHub comments as dispatch. Verify active/inProgress turn or tool activity after follow-up. If unavailable/blocked, AWAITING_AGENT_EXECUTION; if a user-input request is pending, preserve it for the user. Never infer success from assistant commentary.
3. Deduplicate per `(repo,issue,HEAD,defect)`. Reconcile remote `master-task` markers AND checkpoint before publishing. After crash between comment and checkpoint, find the existing remote marker instead of posting again. Preserve comment IDs/decisions. src/integration/supervisor_checkpoint.py supplies tested atomic-save/dedup/ownership helpers; these alone do not guarantee remote exactly-once delivery.
4. Execute useful independent work A→B→C→E→D; F separately. Require authentic source/run/evidence resolvers; missing metadata remains unavailable/unverified. Shared validator and B→E storage adapter do not generate provenance, availability proof, C analytics or simulation outputs. Review/test exact changed heads before integration.
5. For external process wrappers, hold the exclusive cycle_lock over load→remote reconciliation/action→save, not just checkpoint write. Never steal a live or uncertain stale lock. Same-chat scheduling avoids concurrent turns; do not start a second autonomous MASTER worker. Bound temporary reviewers to3 and do not multiply agent chats.
6. Write evidence/logs/status/checkpoint atomically and commit to coordination/master; advance integration/master only with tested changes. Keep PR11 Draft. No main merge, trading, user-data deletion, physical installation or Windows service install. B lifecycle defaults protect raw/source history; E SCM scripts remain dry-run/test-only.
7. Report meaningful changes/completion/failure/user action; don't repeat unchanged snapshots. If no HEAD changes, perform needed audits, fixture/adversarial tests or adapters. Do not rerun settled tests repeatedly without new concerns. Before ending a turn leave the exact next action and configured schedule status, including unverified automation invocation if applicable.

## Local identity and durable repository state

CHECKPOINT.json is portable project state: reviewed HEADs, CI, task marker/comment IDs, evidence and next work. Local .master-local-state.json (gitignored) contains Desktop chat IDs, run IDs and exact worktree location; do not publish local chat telemetry as project evidence. Do not modify automation TOML or scheduler databases directly: automation changes go through automation_update.

## Current verification

Agent A–E follow-ups were sent only after the user's explicit permission, and real active/inProgress turns were observed on2026-11-07T14:28Z. F was already active, so was not re-dispatched. See CHECKPOINT and local state for precise observations. That verifies agent execution, not the recurring MASTER schedule.

