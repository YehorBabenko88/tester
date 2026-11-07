# D/E/F/B integration contract and remaining gates

This is a proposed integration contract from G, not a claim that other agents have
accepted or implemented it. Coordinate in GitHub integration issues linked from
the Draft PR. No files owned by A-F are modified.

## F — strategy and simulation

G accepts stored strategy IDs + immutable 40-hex revision + validation ID. It never
compiles or evaluates prompt text or a strategy DSL. F owns the declarative schema,
strategy preview, costs/slippage/gaps policy and deterministic run artifacts.

E injects `simulation_gate(command)` backed by trusted F/B records. It must authorize
all referenced objects against command.actor/scope and return exactly:

```json
{
  "validation_id": "validation-1",
  "strategy_id": "ma-cross",
  "strategy_revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "snapshot_id": "market-1",
  "coverage": "complete",
  "preview": {"entry_rule": "ma-cross", "fees_bps": "5", "seed": 42},
  "source_ids": ["bybit:archive:1"]
}
```

Preview must include F's resolved strategy/risk/execution assumptions. Missing gate,
partial data, mismatched validation/strategy/snapshot or changed evidence fails
closed. Preview is persisted, human approved and rechecked on submit. G currently
requires complete coverage for simulations; any future partial-data policy needs
an explicit schema/policy revision. No automatic natural-language simulation.

## Result and artifact envelope v1.0

```json
{
  "schema_version": "1.0",
  "data": {"run_id": "run-1", "status": "complete"},
  "provenance": {
    "snapshot_id": "market-1",
    "source_ids": ["bybit:archive:1"],
    "coverage": "complete",
    "code_revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  },
  "artifacts": [{
    "artifact_id": "equity-1",
    "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "media_type": "image/png",
    "size_bytes": 2048
  }]
}
```

Results <=256 KiB, nesting <=12, <=100 artifacts, <=100 provenance references.
All decimal prices/amounts are strings in domain contracts. Artifact IDs are opaque;
no paths/URLs/HTML, byte sizes <=100 MB, types JSON/PNG/plain text. E owns authorized
byte retrieval, digest checking and canonical artifact storage; this PR exposes
manifests only. Raw provider logs are not accepted as events. Store sanitized logs
as immutable text artifacts and reference their digest/ID.

Source IDs and snapshot IDs are **references to B's canonical provenance**, not a
replacement for MASTER #8's full source kind/hash/request/precision/ingestion-run
records. B/E must resolve and verify those records and availability. A nonempty
reference is structurally validated here, not independently attested by G.

Development results must contain exactly mode=isolated-worktree-draft-pr, matching
branch and base_revision, head_revision, positive pr_number and tests_artifact_id
referencing an output artifact. A trusted development provider must verify actual
worktree isolation, tests and Draft status in GitHub. G does not execute Git/code,
verify remote PR state or hotpatch the running service.

## D — bounded overlay projection v1.0

`chart.overlay` result.data is validated by `validate_overlay`. It contains exactly:
schema_version, run_id, revision, venue=bybit, category, symbol, interval,
start_ms, end_ms, availability, coverage, provenance, markers, levels, next_cursor.
Availability follows MASTER #8: AVAILABLE/PARTIAL/UNAVAILABLE/UNVERIFIED. Coverage:
complete/partial/unavailable, separate from availability. Exact command market,
range, run, revision and timeframe must match; result and overlay provenance match.

Each page has <=500 markers and <=500 levels. Pagination cannot silently truncate:
provider supplies next_cursor, and D fetches until null. Keep requested revision
and range fixed while paging; E rejects a cursor from another scope/run/revision.

Marker shape: `{timestamp_ms,kind,price,trade_id,fill_id,reason}`.
`kind=fill` requires stable persisted trade_id and fill_id; `kind=signal` requires
both null. UTC timestamp is inside [start_ms,end_ms). reason is a bounded rule/event
reference, not HTML. F/E must attest that fill IDs exist; strings alone prove no fill.

Level shape: `{start_ms,end_ms,price,kind,rule_id}`. kind is entry/sl/tp/research;
range must lie within the requested page, nonempty. Price is a positive decimal
string, never float. SL/TP are explicit levels, not inferred bid/ask data.

D renders escaped text and approved data primitives only; no arbitrary HTML/JS.
Distinguish persisted fills from signals; retain availability, coverage, source
references, snapshot and code revision. Clicking a trade uses chart.navigate with
typed market/timeframe/range, rather than executable frontend instructions.

## Deployment/integration acceptance still required

- E: loopback HTTP + auth/Host/Origin/CSRF ingress, durable credential provisioning,
  domain ACLs, revocation before dispatch, bounded provider registry, cooperative/OS
  cancellation, journal reconciliation, artifact bytes, external audit anchoring.
- F/B: canonical evidence/snapshot validation, deterministic offline runs and
  persisted provenance/fills; match fixtures to this projection.
- D: renderer/cursor integration and visual tests, partial/unverified states.
- MASTER: integrated multi-provider acceptance and actual Desktop installation.

No market-data downloads, real trading, physical deployment, Desktop settings
changes, service install or merge are performed by this implementation task.
