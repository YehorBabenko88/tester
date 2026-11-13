"""Read-only bridge refuses false F admission and cross-market composition."""
from types import SimpleNamespace

import pytest

from src.integration import research_preview as bridge
from src.integration.qualified_snapshot import SnapshotRequest


def request():
    return SnapshotRequest("bybit", "linear", "BTCUSDT", "1", 0, 60000)


def test_preview_refuses_f_and_binds_digests(monkeypatch):
    snapshot = SimpleNamespace(
        mode="REAL", snapshot_id="unverified:abc",
        dataset_revision="abc", evidence_digest="def",
        reasons=("historical_visibility_not_proven",),
    )
    monkeypatch.setattr(bridge, "read_canonical_snapshot", lambda store, req: snapshot)
    monkeypatch.setattr(bridge, "calculate", lambda *args, **kwargs: {
        "market": {"venue": "bybit", "category": "linear", "symbol": "BTCUSDT"},
        "reasons": ["no_complete_range_proof"],
        "admitted_to_f": False, "bars": [],
    })
    result = bridge.research_preview(object(), request(), [], tick_size="0.5")
    assert result["f_input"] is None
    assert result["g_e_result"] is None
    assert result["snapshot_admitted"] is False
    assert result["status"] == "UNVERIFIED"
    assert result["proof"]["dataset_revision"] == "abc"


def test_cross_market_rejected(monkeypatch):
    monkeypatch.setattr(bridge, "read_canonical_snapshot", lambda *args: SimpleNamespace(
        mode="REAL", snapshot_id="x", dataset_revision="x",
        evidence_digest="x", reasons=(),
    ))
    monkeypatch.setattr(bridge, "calculate", lambda *args, **kwargs: {
        "market": {"venue": "bybit", "category": "spot", "symbol": "BTCUSDT"},
        "reasons": [], "bars": [],
    })
    with pytest.raises(ValueError, match="execution_candle_scope_mismatch"):
        bridge.research_preview(object(), request(), [], tick_size="0.5")


def test_wrong_request_type_rejected():
    with pytest.raises(ValueError, match="typed_snapshot_request_required"):
        bridge.research_preview(object(), {}, [], tick_size="0.5")
