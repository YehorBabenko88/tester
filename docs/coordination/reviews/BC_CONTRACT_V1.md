# Original B/C opt-in contract gates

Implemented `work/repo/contract_checks/test_bc_contract.py` using actual pinned exports `inspect` and `aggregate`, with no product changes or invented adapter APIs.

- B: `801297fb3f7e20aba5341f2aae3a5c58074b704b`, `src/quality/candles.py`.
- C: `0070b5fcb9180a79b213698c99ea289df4442383`, `src/analytics/footprint.py`.

The exact bytes from read-only `git show` are saved under `contract_checks/fixtures/b_candles_initial.py` and `c_footprint_initial.py`. Every gate run compares each snapshot byte-for-byte to the pinned Git object before isolated module execution. Source SHA-256 values and exact commit IDs are included in the machine-readable JSON appended to `work/repo/docs/coordination/evidence/contract-bc.log`.

Executed from `work/repo`: `../venv/Scripts/python.exe contract_checks/test_bc_contract.py > ../contract-bc-console.log`.

**Exit 1. Eight test methods ran; twelve assertion/subtest failures, zero errors.** These are intentional visible nonconformance failures, not expected-failure decorators or passing assertions about known bugs.

B fails empty-page completeness, NaN and Infinity rejection, decimal-exact invalid OHLC validation, and schema version/provenance metadata. The decimal counterexample has open `1.00000000000000000002` above high `1.00000000000000000001`; binary float conversion loses the violation.

C fails stable-ID deduplication (duplicate trade counted twice), missing-side unknown/null delta (the current export raises `KeyError`, reported as explicitly unavailable capability), exact permutation-invariant decimal totals, and required version/provenance/timestamp_ms metadata. The precision case aggregates `1000000000000000000000000000`, `0.6`, `0.6`; input permutations produce two different totals under the default Decimal context. No claim is made that C infers missing side as Sell: the actual observed behavior is failure to represent that record.

The opt-in directory has no `__init__.py`, so normal root unittest discovery does not recurse into these intentionally red gates. No engine edits, Git commits, branch changes, comments or pushes were made. All calculations used local fixtures; no network market data or database was used.

