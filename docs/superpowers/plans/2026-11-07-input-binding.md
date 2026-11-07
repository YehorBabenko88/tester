# Canonical input binding implementation plan

Spec: Issue3 comment6709081990, marker C-152823-B-e236-canonical-input-binding.
Scope: tester-owned src/analytics binding only; existing C mathematics unchanged.

1. Pin B e236395684d661f9464a62a0f6f67803d9000de6 models, MASTER d945b23ab940b67f569ed85aa4c8d6e9684f8149 trade projection/shared v1, and E b15b1ce12633a134b19cb71a57d7e841a7120579 bound_response as unchanged test references.
2. Add failing synthetic contract tests for B/MASTER projection, all run refs, exact ns/Decimal and full ID dedup/conflict; UNKNOWN/partial and unknown-side behavior; actual information cutoff; fail-closed missing source run metadata; unsupported extensions.
3. Implement thin bind_storage_trades / bind_canonical_trades and TradeBinding.response. Reuse injected MASTER projections and existing C aggregate/footprint; no mathematics, storage reads, HTTP routing or schema expansion.
4. Run complete C branch suite including actual pinned E bound_response and shared validator; inspect reference hashes and unchanged mathematics. Independent review, fix regressions, publish Draft PR20 follow-up SHA and exact test log in Issues3/6. No merge/deploy/live/full-pipeline claim.
