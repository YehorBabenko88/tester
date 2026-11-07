# Engine migration: strategy-bar-1.0.0 -> strategy-bar-1.1.0

Corrective follow-up to MASTER PR19 comment 6708269302. Strategy DSL and shared
market `schema_version='1.0'` do not change. Engine semantics and run hashes do change;
old engine checkpoints are rejected by the existing version gate. Rerun pinned inputs
on the chosen engine version rather than relabeling an old result as corrected.

## Required liquidity envelope

Legacy `{quantity,evidence_refs}` is rejected. Do not migrate by inventing times or
quality. A/B/C/E must supply verified finer historical evidence for the entire bar:

```json
{
  "quantity": "1",
  "start_ms": 60000,
  "end_ms": 120000,
  "available_at_ms": 120000,
  "evidence_refs": ["fixture:executions"],
  "availability": {"status": "AVAILABLE", "reason": null},
  "coverage": {
    "status": "COMPLETE", "start_ms": 60000, "end_ms": 120000,
    "evidence_refs": ["fixture:executions"]
  },
  "provenance": [{
    "source_kind": "synthetic_fixture", "source_ref": "fixture:executions",
    "source_hash": null, "ingestion_run": "test", "timestamp_precision": "ms",
    "source_timestamp": null
  }]
}
```

This explicit synthetic example asserts a whole-bar bound available at its close.
If actual evidence became available later, the current close-step model rejects it;
it must not be backdated. Real quote/tick temporal matching remains unavailable.
Exploratory price coverage policy never upgrades partial execution evidence.

## Result additions and corrected meaning

- All fills and overlays add `known_at_ms`.
- All fills add `liquidity_evidence_refs` (empty without a participation budget).
- A fill using whole-bar capacity is always `time_precision='bar'`; its timestamp
  is only a representative marker at end_ms-1. Use the full interval and do not
  reveal it in historical replay before known_at_ms=end_ms.
- `BAR_CAPACITY_TIMING_UNCERTAIN` warns that the whole-bar bound does not establish
  exact opening liquidity. Its quantity can legitimately change with that bar's
  evidence, but it cannot change an earlier precise opening fill because none is claimed.
- Such an entry cannot obtain a same-bar target because its entry timing is uncertain.
- A triggered protective exit is retained even at zero fill, with requested/filled
  quantities, trigger_window and trigger_known_at_ms. Subsequent fills use that same
  order/reason at later eligible evidence; rebound and gaps do not untrigger it.
- End-of-run cancellation still applies to residual orders. No execution is fabricated
  at exhaustion, and the remaining position stays visible in results.
