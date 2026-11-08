# Pinned F research adapter implementation plan

**Goal:** Run the actual pinned F engine against an explicitly synthetic dataset,
retain immutable inputs/results and project every visible fill into the agreed
K/L research-chart v2 contract without changing installed services.

**Architecture:** Vendor exact same-repository F source commit
998c5bc89d1b35c9ad3047b1e50dcb1295120e31 at its original package names. A pure
`src/integration/strategy_lab.py` adapter runs F, verifies replay and complete
fill/order/entry-position/trade correspondence, serializes the full evidence bundle
and produces bounded chart/candle primitives. K owns canonical storage/auth/jobs;
L owns rendering. Synthetic metadata never certifies real history.

**Spec:** Coordinator integration-wave request and K/L messages; strict
tester.research-chart/2 plus candles and actual entry/exit execution side.

1. [x] Normally merge integration53a51dab, preserve all raw evidence attributes,
   run existing focused tests and publish merge0ddc4be before new implementation.
2. [x] Agree bundle/marker/candle interfaces with K/L and coordinator.
3. [ ] Add failing tests for actual-engine deterministic result, full marker
   correspondence, synthetic-only admission, tamper rejection and cutoff scope.
4. [ ] Vendor exact F Python sources, upstream tests and packaged published fixture
   bytes with Git/SHA-256 pin manifests and byte-preserving attributes.
5. [ ] Implement bounded pure adapter and serialization; no alternate engine,
   static success backend, SQL, authentication or installed path access.
6. [ ] Run upstream F tests and adapter/adverse tests, verify source hashes and
   packaging, publish source SHA and contracts for root/K/L end-to-end wiring.
