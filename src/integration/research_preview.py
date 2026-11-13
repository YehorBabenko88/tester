"""Read-only provenance bridge for execution metrics and the B qualified snapshot.

This intentionally does not register an F input, create an execution job or
upgrade historical visibility. F authorization remains in the reviewed B/F/G path.
"""
from src.analytics.research_metrics import calculate
from src.integration.qualified_snapshot import (
    QualifiedSnapshot,
    SnapshotRequest,
    read_canonical_snapshot,
)
from src.strategy.dsl import digest


def research_preview(store, request: SnapshotRequest, trades, *, tick_size: str):
    """Bind exact calculation evidence to the same requested candle revision.

    The caller must supply canonical execution records from a separately
    verified source; this function does not claim a database read of trades.
    """
    if not isinstance(request, SnapshotRequest):
        raise ValueError("typed_snapshot_request_required")
    snapshot = read_canonical_snapshot(store, request)
    if not isinstance(snapshot, QualifiedSnapshot) or snapshot.mode != "REAL":
        raise ValueError("unexpected_snapshot_mode")
    if request.interval == "M":
        raise ValueError("calendar_month_requires_variable_interval")
    interval_ms = {
        "1": 60000, "3": 180000, "5": 300000, "15": 900000,
        "30": 1800000, "60": 3600000, "120": 7200000,
        "240": 14400000, "360": 21600000, "720": 43200000,
        "D": 86400000, "W": 604800000,
    }[request.interval]
    result = calculate(
        trades, start_ms=request.start_ms, end_ms=request.end_ms,
        interval_ms=interval_ms, tick_size=tick_size,
    )
    market = result.get("market")
    if market is not None and any(market[k] != request.scope[k] for k in
                                  ("venue", "category", "symbol")):
        raise ValueError("execution_candle_scope_mismatch")
    proof = {
        "snapshot_id": snapshot.snapshot_id,
        "dataset_revision": snapshot.dataset_revision,
        "snapshot_evidence_digest": snapshot.evidence_digest,
        "metrics_digest": digest(result),
        "requested_scope": request.scope,
        "start_ms": request.start_ms,
        "end_ms": request.end_ms,
    }
    return {
        "schema_version": "tester.research-preview/1",
        "proof": proof,
        "proof_digest": digest(proof),
        "metrics": result,
        "snapshot_admitted": False,
        "f_input": None,
        "g_e_result": None,
        "status": "UNVERIFIED",
        "reasons": sorted(set(snapshot.reasons) | set(result["reasons"])),
    }
