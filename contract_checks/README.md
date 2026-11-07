# Independent producer conformance gates — Issue #8

These are deliberately separate from ordinary coordinator regression tests. They test immutable initial workstream heads, and currently FAIL. A green shared-schema fixture suite does not imply any producer conforms. Do not mark failures expected/pass or count absent implementations as successful.

Run from the repository root after installing requirements-dev.txt; Node is needed for the controlled D DOM probe. A/B/C use read-only git objects, so fetch the specified immutable workstream commits before running in a shallow clone. D/E use source/tree snapshots fetched for exact pinned heads.

```text
python contract_checks/check_a.py --json docs/coordination/evidence/contract-a.json
python contract_checks/test_bc_contract.py
python contract_checks/test_de_contract.py
python -m unittest test_approved_contract -v
```

| Component | Immutable head | Gate/result |
|---|---|---|
| A | 43d0126d28c95f8f5f6362e866bd6e6d50a5e7bb | 7 methods, 6 failures: schema/provenance, invalid archive replacement, pagination/time adapters absent |
| B | 801297fb3f7e20aba5341f2aae3a5c58074b704b | B/C combined 8 methods, 12 assertion/subtest failures: empty/nonnumeric/precision-invalid data and metadata |
| C | 0070b5fcb9180a79b213698c99ea289df4442383 | B/C combined: duplicate IDs, missing-side metadata/null delta capability, order-dependent arithmetic |
| D | 421f22ba3a2c377c16f08b3f0f5b79417078e98f | D/E combined 6 methods, 4 failures: unavailable/partial state reasons discarded |
| E | 36c939fcfb36ec8b8e7e529f1f1cdda8083a3279 | D/E combined: API/schema implementation absent |

Tests assert desired behavior, so these defects produce nonzero exit codes. Missing capabilities are explicit conformance failures rather than guessed adapter implementations. Controlled HTTP/DOM fixtures do not prove live Bybit/PostgreSQL or browser end-to-end acceptance. No real orders, secrets or live datasets are used in these conformance fixtures.

For corrected branches, add concrete adapter tests against the newly published callable APIs and exact new HEAD, keeping historical evidence. Do not merely update a SHA while retaining assumptions about old function signatures. The final integration gate requires every producer/consumer pair green at the SHAs actually integrated, including shared schema validation, stable identity, source precision, coverage and provenance propagation.

