# Control plane implementation plan

Goal: implement issue #18 within G-owned paths only. Python standard library core,
SQLite transactions, transport-independent API and optional MCP SDK adapter.
Spec: issue #18 and ARCHITECTURE.md. Work in the dedicated cloned G branch.
The user explicitly requested immediate implementation; execute inline.

## Constraints and review focus

No trading, shell, SQL from input, live code patch, deployment or merge. Trusted
providers alone perform domain work. E owns HTTP/service integration, D rendering,
F declarative strategies. Fail closed when providers/evidence are unavailable.
Review stale approvals, cross-agent reads, duplicate races, late worker writes,
untrusted provider output and restart after side effects.

## Tasks

- [x] Add failing security/lifecycle tests in tests/control/test_plane.py.
- [x] Extend commands.py with strict per-action parameters and a versioned envelope;
  preserve the low-level legacy validator for compatibility, never admit legacy
  envelopes to the new control plane. Add contracts.py for provenance/overlay data.
- [x] Add policy.py: expiring token authentication and per-action/scope grants.
- [x] Add plane.py: durable plans, bound human approvals, idempotent submission,
  quotas, events, provenance, cancellation, leases and interrupted-job recovery.
- [x] Add bridge.py: MCP SDK tool factory using a fixed authenticated principal;
  expose no human approval tool, arbitrary execution or token provisioning tool.
- [x] Run all control tests and repository tests, review security boundaries.
- [x] Document E API, D/F contracts, MCP setup and limitations; publish integration
  tasks, commit, push G branch and create Draft PR with exact HEAD and evidence.

Verification: python -m unittest discover -s tests/control -v;
python -m unittest discover -v. Use real temporary SQLite databases and concurrent
connections to test atomicity, restart, cancellation and isolation.

## Execution ledger / verification

2026-11-07, Windows / Python 3.14:

- Baseline: 9 control tests and 9 repository tests passed.
- Final: 38 control tests (including actual MCP SDK 1.30.0 STDIO subprocess and
  authenticated ephemeral HTTP request) and all 9 repository tests passed.
- `pip check`: no broken requirements. `git diff --check`: clean.
- Independent review found future-window admission, provider-dependent idempotent
  planning, audit scope pagination and rate checks after expensive validation.
  Each finding received a failing regression test, fix and passing verification.
- Added actual two-agent global-quota race and two-approved-plan config CAS race.
- Integration requests published: E #21, F/B #22, D #23. Agreement pending.
- Ruling: G stores control metadata only in SQLite, leaving canonical market
  evidence in B's PostgreSQL; E owns its HTTP/service/provider integration.
- Ruling: config applies desired policy in the journal only. Missing build SHA is
  explicitly null for internal config results; E must pass its exact release SHA.
- Ruling: keep legacy 1.0 helpers for compatibility, but admit only v1.1 at API.
- No trading, deployment or merge. Draft PR is the requested integration path.
