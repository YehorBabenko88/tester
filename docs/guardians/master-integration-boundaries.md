# TESTER MASTER: duplicate ownership and integration gates

Status: Draft PR #89; **not** a release authorization.

## Guardian supervisors: one policy authority, one durable authority

| Module | Sole responsibility | Must not do |
| --- | --- | --- |
| `src/api/guardian_policy.py` | Stateless, deterministic classification of event evidence | Service control, SQL writes, network or trading |
| `src/api/guardian_supervisor.py` | Pure `GuardianSupervisor.propose`: component/action ownership, environment gate and stable idempotency key | Disk leases, cross-reboot recovery or independent execution |
| `src/api/guardians/supervisor.py` | `DurableSupervisor`: SQLite-only receipts, global idempotency, lease epochs, restart budget, kill switch | Reclassify policy, trust a self-reported after-check or run an effector |
| Code/Data/Storage/Exchange modules | Their own read-only evidence and proposal validation | Install services, maintain a second durable journal or bypass MASTER admission |
| `src/api/guardians/windows_install_gate.py` | Pure manifest/ownership/typed deletion authorization | Delete files or stop services |
| `src/api/guardians/windows_install_plan.py` and `windows_release_lifecycle.py` | C:\TesterLab layout and ordered, non-effectful actions | Claim signature verification or execute the plan |

The two Supervisor classes are *not* competing service managers. Only
`DurableSupervisor` persists admission authority. `GuardianSupervisor._seen`
is process-local defense-in-depth; it resets on process creation and must never
be used as the global deduplication source. The wrapper must persist every
accepted proposal and prevent repeated reservation after a completed lease.
Never declare success from `verified=True` alone: a separately authenticated,
independently executed after-check is not implemented.

## Canonical installed runtime contract is NOT approved

Current policy and Code/Data agents explicitly require
`environment=tester_preview`. The new desktop architecture uses
`C:\TesterLab` and a separate dedicated PostgreSQL instance. These are **not
equivalent**. Do not rewrite event labels, monkeypatch policy or accept an
untrusted `ownership_verified=True` assertion to make them equivalent.

A future versioned identity document must pin the full 40-character release
SHA, signed package digest, install UUID, canonical root, application service
names and binary digests, dedicated database cluster and non-5432 port,
task/shortcut identities, and an external trusted signer. The event adapter
must independently validate the live service ImagePath, filesystem ancestry,
PostgreSQL cluster identity, and signature. A new `tester_lab` environment
must have its own negative tests before enabling even proposal-only admission.

## Non-duplicated Windows installer target

The supported future installer will be rooted only under `C:\TesterLab`.
The existing `scripts/windows/Install-Tester.ps1` installs a repo-local
`.venv/runtime`; `scripts/windows_preview/Install-TesterPreview-ADMINIT-v3.5.ps1`
uses LocalAppData/ProgramData and distinct ports. Neither is authorized as
the new installer. No existing installation may be adopted or removed by
name alone. Side effects require a separately reviewed signed Windows
adapter, exclusive lock and disposable physical validation.

The only available release lifecycle functions are pure plans. The
`scripts/windows/Write-TesterShortcutsFixture.ps1` helper creates two
shortcuts only inside GitHub Actions' disposable `RUNNER_TEMP`, never on a
user's actual desktop. It must not be treated as an installer.

## Cleanup inventory for separate PR #94

Potential *deprecation* (not deletion) subjects, conditional on dependency
search, deployment pins and approved exact-head CI:

- `scripts/windows/Install-Tester.ps1`, `Start-Tester.ps1`, historical
  repo-local runtime installer assets
- `scripts/windows_preview/Install-TesterPreview-ADMINIT-v3*.ps1`,
  preview-only shortcuts, task definitions and legacy Preview evidence
- Historical `feature/adminit-preview-clean-install-v3-20261118` and
  unmerged Preview branches, only after open PR/deployment/tag consumers are
  verified absent

`src/api/observed_volatility.py` is not Guardian-owned. Its only difference
in PR #89 is a Ruff formatting blank line; there is no math change. The
standalone formatting correction should be separated from Guardian
implementation after independent CI confirms a safe split. Do not drop it
from the PR while the repository-wide formatter requires the change without
first changing the base or agreeing a separate format PR.

## Release integration order

1. MASTER foundation exact-head Python, Platform and targeted Windows/Linux
   fault workflow all PASS; no unrelated tests silently skipped.
2. Resolve signed `tester_lab` identity and independently verified after-check.
3. Pin each Guardian HEAD; non-force refresh each child from accepted MASTER
   SHA and demand owned tests + required exact-head CI.
4. Create an **independent** reviewed combined integration branch from the
   accepted base; do not merge open Draft PRs into `main` to simulate progress.
5. Disposable Windows integration: API, worker, dedicated PostgreSQL,
   localhost terminal, boot/restart recovery, two exact shortcuts, typed
   `DELETE TESTER` and full owned-only deletion with explicit partial-failure
   receipts; verify untouched system PostgreSQL 5432 before and after.
6. Only then sign an immutable artifact, obtain physical ADMINIT acceptance,
   and request human release approval. Until then: **NO_GO**.
