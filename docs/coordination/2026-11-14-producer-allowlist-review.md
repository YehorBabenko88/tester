# Producer allowlist delta — review pending

> This is an inventory, NOT an approved manifest. The enforced allowlist remains unchanged.

Exact PR heads: DATA `4581e415`, RESEARCH `e76b08db`, UI `8819b86f`, OPS `36774155`.

## DATA — 4 unreviewed paths

- `.github/workflows/data-private-generation-review.yml`
- `docs/coordination/data72-private-generation-blob-manifest.json`
- `docs/coordination/data72-private-generation-replay.md`
- `tests/test_data_private_generation_compat.py`

## RESEARCH — 5 unreviewed paths

- `.github/workflows/research-private-generation.yml`
- `docs/research_private_generation_e2e_commands.md`
- `docs/research_private_generation_source_ledger.json`
- `scripts/research_private_generation_issue59.py`
- `tests/platform/test_research_private_generation_issue59.py`

## UI — 2 unreviewed paths

- `.github/workflows/ui74-private-generation-e2e.yml`
- `scripts/ui74_private_generation_ci.py`

## OPS — 14 unreviewed paths

- `.github/workflows/ops-master-v3-security-audit.yml`
- `.github/workflows/ops-private-generation-ci.yml`
- `docs/ops_master_5ee448fa_security_audit_20261114.md`
- `docs/ops_master_77_private_generation_integration.md`
- `docs/ops_master_d6d6548_security_audit_20261114.md`
- `docs/ops_master_f3417969_security_audit_20261114.md`
- `docs/ops_private_generation_protocol.md`
- `scripts/ops_private_generation_prototype.py`
- `tests/platform/test_ops_master_composer_v3_security.py`
- `tests/platform/test_ops_master_composer_v4_security.py`
- `tests/platform/test_ops_master_composer_v5_security.py`
- `tests/platform/test_ops_private_generation_crash.py`
- `tests/platform/test_ops_private_generation_prototype.py`
- `tests/platform/test_ops_private_generation_recovery.py`

## Admission gates

- Review each path's content and Git blob at the exact immutable producer head.
- Separate runtime files from CI/docs/prototype-only additions; do not automatically include all.
- Require a new externally reviewed manifest digest and exact source pins.
- Re-run private generation security negatives and combined PG14/18, FastAPI/Chromium E2E on one immutable candidate.
- No installation, native launcher activation, real trading, main or physical PC4/PG5432 changes.
