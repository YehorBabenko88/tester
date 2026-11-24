# Guardian workstream: storage

Issue: https://github.com/YehorBabenko88/tester/issues/86
Foundation: https://github.com/YehorBabenko88/tester/pull/89

Owned code/tests: src/api/guardians/storage_guardian.py; tests/platform/test_storage_guardian.py

Scope: Own-runtime inventory, disk quotas, dry-run temp retention and explicit owned-only cleanup; no history/WAL deletion.

## Constraints

- Read parent contract: docs/coordination/2026-11-18-tester-four-guardian-agents-p0.md.
- Separate branch and Draft PR into feature/tester-four-guardian-agents-p0-20261118; never write to master or running ADMINIT Preview.
- Default OFF; observe/classify before any effectors. No model-generated executable shell or SQL, never mutate data without original source evidence.
- Explicit negative tests for unknown inputs, ownership, resource budgets, source mismatch and Windows shutdown.
- Do not modify modules owned by another Guardian; coordinate shared changes with MASTER issue #88.
- CI exact HEAD, report PASS/FAIL and stop at NO-GO unless independent physical acceptance exists.
