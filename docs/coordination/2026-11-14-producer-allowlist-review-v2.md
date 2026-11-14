# Current producer file delta — review pending

> Not an approved allowlist. Do not automatically admit these files into MASTER source composition.

## DATA — 9d7e45be0872c4c5efa17c06bc107e1dc6a0d2fb
Approved 21, current 31, pending 10

- `.github/workflows/data-private-generation-review.yml`
- `.github/workflows/data72-25file-private-generation.yml`
- `docs/coordination/data72-25file-ops-canonical-manifest.json`
- `docs/coordination/data72-298d-allowlist-final-review.md`
- `docs/coordination/data72-current-head-immutable-review.json`
- `docs/coordination/data72-private-generation-blob-manifest.json`
- `docs/coordination/data72-private-generation-replay.md`
- `scripts/verify_data72_head_manifest.py`
- `tests/test_data_25file_private_generation.py`
- `tests/test_data_private_generation_compat.py`

## RESEARCH — af143bb73277e7bffea8ae76d84fd0ed7a707ab0
Approved 15, current 20, pending 5

- `.github/workflows/research-private-generation.yml`
- `docs/research_private_generation_e2e_commands.md`
- `docs/research_private_generation_source_ledger.json`
- `scripts/research_private_generation_issue59.py`
- `tests/platform/test_research_private_generation_issue59.py`

## UI — 2de6428b48e2b468d15aa8c9e40e4e47719e168f
Approved 37, current 41, pending 4

- `.github/workflows/ui74-private-generation-e2e.yml`
- `docs/integration/ui74-master77-pending-allowlist-additions.json`
- `scripts/ui74_private_generation_ci.py`
- `tests/platform/test_ui_pending_allowlist.py`

## OPS — 9c0ee3a44e78d16d8beb34fbaf2973ee2e126386
Approved 65, current 82, pending 17

- `.github/workflows/ops-master-v3-security-audit.yml`
- `.github/workflows/ops-private-generation-ci.yml`
- `docs/ops_73_allowlist_immutable_handoff_20261114.md`
- `docs/ops_master_5ee448fa_security_audit_20261114.md`
- `docs/ops_master_77_private_generation_integration.md`
- `docs/ops_master_d6d6548_security_audit_20261114.md`
- `docs/ops_master_f3417969_security_audit_20261114.md`
- `docs/ops_private_generation_protocol.md`
- `docs/ops_producer_allowlist_delta_36774155.json`
- `scripts/ops_private_generation_prototype.py`
- `tests/platform/test_ops_master_composer_v3_security.py`
- `tests/platform/test_ops_master_composer_v4_security.py`
- `tests/platform/test_ops_master_composer_v5_security.py`
- `tests/platform/test_ops_private_generation_crash.py`
- `tests/platform/test_ops_private_generation_prototype.py`
- `tests/platform/test_ops_private_generation_recovery.py`
- `tests/platform/test_ops_producer_allowlist_delta.py`

## Gate
Review exact Git blobs and roles of each path. Keep source-only and CI-only paths distinct; approve only immutable reviewed entries and rerun all checks.
