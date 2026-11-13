"""Read-only bridge refuses false F admission and cross-market composition."""
import pytest

from src.integration import research_preview as bridge
from src.integration.qualified_snapshot import QualifiedSnapshot, SnapshotRequest


def request():
    return SnapshotRequest("bybit", "linear", "BTCUSDT", "1", 0, 60000)


def test_preview_refuses_f_and_binds_digests(monkeypatch):
    snapshot = QualifiedSnapshot("REAL", "abc", None, "def", False,
                                 ("historical_visibility_not_proven",), None, "{}")
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
    assert result["proof"]["execution_input_digest"] == bridge.digest([])


def test_cross_market_rejected(monkeypatch):
    monkeypatch.setattr(bridge, "read_canonical_snapshot", lambda *args: QualifiedSnapshot("REAL", "x", None, "x", False, (), None, "{}"))
    monkeypatch.setattr(bridge, "calculate", lambda *args, **kwargs: {
        "market": {"venue": "bybit", "category": "spot", "symbol": "BTCUSDT"},
        "reasons": [], "bars": [],
    })
    with pytest.raises(ValueError, match="execution_candle_scope_mismatch"):
        bridge.research_preview(object(), request(), [], tick_size="0.5")


def test_wrong_request_type_rejected():
    with pytest.raises(ValueError, match="typed_snapshot_request_required"):
        bridge.research_preview(object(), {}, [], tick_size="0.5")


def test_execution_input_must_be_canonical_list():
    with pytest.raises(ValueError, match="canonical_execution_list_required"):
        bridge.research_preview(object(), request(), (), tick_size="0.5")


def test_unaligned_exchange_interval_refused(monkeypatch):
    monkeypatch.setattr(bridge, "read_canonical_snapshot", lambda *args: QualifiedSnapshot(
        "REAL", "x", None, "x", False, (), None, "{}"))
    req = SnapshotRequest("bybit", "linear", "BTCUSDT", "1", 1, 60001)
    with pytest.raises(ValueError, match="request_not_aligned_to_exchange_interval"):
        bridge.research_preview(object(), req, [], tick_size="0.5")


def test_weekly_interval_uses_monday_utc(monkeypatch):
    monkeypatch.setattr(bridge, "read_canonical_snapshot", lambda *args: QualifiedSnapshot(
        "REAL", "x", None, "x", False, (), None, "{}"))
    # 2024-01-01 00:00 UTC was Monday.
    start = 1704067200000
    req = SnapshotRequest("bybit", "linear", "BTCUSDT", "W", start, start + 604800000)
    result = bridge.research_preview(object(), req, [], tick_size="0.5")
    assert result["status"] == "UNVERIFIED"
    assert result["f_input"] is None
