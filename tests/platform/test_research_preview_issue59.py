"""Read-only bridge refuses false F admission and cross-market composition."""

import pytest

from src.integration import research_preview as bridge
from src.integration.qualified_snapshot import QualifiedSnapshot, SnapshotRequest


def request():
    return SnapshotRequest("bybit", "linear", "BTCUSDT", "1", 0, 60000)


def test_preview_refuses_f_and_binds_digests(monkeypatch):
    snapshot = QualifiedSnapshot(
        "REAL", "abc", None, "def", False, ("historical_visibility_not_proven",), None, "{}"
    )
    monkeypatch.setattr(bridge, "read_canonical_snapshot", lambda store, req: snapshot)
    monkeypatch.setattr(
        bridge,
        "calculate_v2",
        lambda *args, **kwargs: {
            "market": {"venue": "bybit", "category": "linear", "symbol": "BTCUSDT"},
            "reasons": ["no_complete_range_proof"],
            "admitted_to_f": False,
            "bars": [],
        },
    )
    result = bridge.research_preview(object(), request(), [], tick_size="0.5")
    assert result["f_input"] is None
    assert result["g_e_result"] is None
    assert result["snapshot_admitted"] is False
    assert result["status"] == "UNVERIFIED"
    assert result["proof"]["dataset_revision"] == "abc"
    assert result["proof"]["execution_input_digest"] == bridge.digest([])


def test_cross_market_rejected(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "read_canonical_snapshot",
        lambda *args: QualifiedSnapshot("REAL", "x", None, "x", False, (), None, "{}"),
    )
    monkeypatch.setattr(
        bridge,
        "calculate_v2",
        lambda *args, **kwargs: {
            "market": {"venue": "bybit", "category": "spot", "symbol": "BTCUSDT"},
            "reasons": [],
            "bars": [],
        },
    )
    with pytest.raises(ValueError, match="execution_candle_scope_mismatch"):
        bridge.research_preview(object(), request(), [], tick_size="0.5")


def test_wrong_request_type_rejected():
    with pytest.raises(ValueError, match="typed_snapshot_request_required"):
        bridge.research_preview(object(), {}, [], tick_size="0.5")


def test_execution_input_must_be_canonical_list():
    with pytest.raises(ValueError, match="canonical_execution_list_required"):
        bridge.research_preview(object(), request(), (), tick_size="0.5")


def test_unaligned_exchange_interval_refused(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "read_canonical_snapshot",
        lambda *args: QualifiedSnapshot("REAL", "x", None, "x", False, (), None, "{}"),
    )
    req = SnapshotRequest("bybit", "linear", "BTCUSDT", "1", 1, 60001)
    with pytest.raises(ValueError, match="request_not_aligned_to_exchange_interval"):
        bridge.research_preview(object(), req, [], tick_size="0.5")


def test_weekly_interval_uses_monday_utc(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "read_canonical_snapshot",
        lambda *args: QualifiedSnapshot("REAL", "x", None, "x", False, (), None, "{}"),
    )
    # 2024-01-01 00:00 UTC was Monday.
    start = 1704067200000
    req = SnapshotRequest("bybit", "linear", "BTCUSDT", "W", start, start + 604800000)
    result = bridge.research_preview(object(), req, [], tick_size="0.5")
    assert result["status"] == "UNVERIFIED"
    assert result["f_input"] is None


def _inverse_rows():
    from src.analytics.core import normalize_trade

    return [
        normalize_trade(
            {"time": "1", "price": price, "size": "100", "side": "Buy", "execId": exec_id},
            category="inverse",
            symbol="BTCUSD",
            source_ref={
                "source_kind": "bybit_rest",
                "source_ref": "fixture-rest",
                "source_hash": None,
                "ingestion_run": "test-run",
                "timestamp_precision": "ms",
                "source_timestamp": "1",
            },
        )
        for price, exec_id in (("100", "a"), ("200", "b"))
    ]


def test_preview_writes_execution_metrics_v2(monkeypatch):
    # Before the consumer migration the preview wrote tester.execution-metrics/1
    # (no quantity_unit).  New writes are /2 only.
    from src.analytics.core import normalize_trade
    from src.analytics.research_metrics import METHOD_V2, metrics_read_contract

    monkeypatch.setattr(
        bridge,
        "read_canonical_snapshot",
        lambda *args: QualifiedSnapshot("REAL", "r", None, "e", False, (), None, "{}"),
    )
    rows = [
        normalize_trade(
            {"time": "1", "price": "100", "size": "2", "side": "Buy", "execId": "a"},
            category="linear",
            symbol="BTCUSDT",
            source_ref={
                "source_kind": "bybit_rest",
                "source_ref": "fixture-rest",
                "source_hash": None,
                "ingestion_run": "test-run",
                "timestamp_precision": "ms",
                "source_timestamp": "1",
            },
        )
    ]
    result = bridge.research_preview(object(), request(), rows, tick_size="1")
    assert result["metrics"]["method"] == METHOD_V2
    assert result["metrics"]["quantity_unit"] == "base"
    assert metrics_read_contract(result["metrics"])["status"] == "CURRENT"
    assert result["proof"]["metrics_digest"] == bridge.digest(result["metrics"])


def test_preview_inverse_refused_by_snapshot_scope_not_metrics(monkeypatch):
    # The metrics are /2 (inverse-correct); inverse is refused by the B
    # snapshot scope (spot/linear candles only), and an inverse execution set
    # can never be bound to a linear candle snapshot.
    with pytest.raises(ValueError, match="unsupported_snapshot_scope"):
        SnapshotRequest("bybit", "inverse", "BTCUSD", "1", 0, 60000)
    monkeypatch.setattr(
        bridge,
        "read_canonical_snapshot",
        lambda *args: QualifiedSnapshot("REAL", "r", None, "e", False, (), None, "{}"),
    )
    with pytest.raises(ValueError, match="execution_candle_scope_mismatch"):
        bridge.research_preview(object(), request(), _inverse_rows(), tick_size="1")


def test_preview_spot_binds_base_unit_v2_metrics(monkeypatch):
    # Spot stays in the B snapshot scope; its /2 metrics are in base units.
    from src.analytics.core import normalize_trade

    monkeypatch.setattr(
        bridge,
        "read_canonical_snapshot",
        lambda *args: QualifiedSnapshot("REAL", "r", None, "e", False, (), None, "{}"),
    )
    rows = [
        normalize_trade(
            {"time": str(ts), "price": price, "size": "100", "side": side, "execId": exec_id},
            category="spot",
            symbol="BTCUSDT",
            source_ref={
                "source_kind": "bybit_rest",
                "source_ref": "fixture-rest",
                "source_hash": None,
                "ingestion_run": "test-run",
                "timestamp_precision": "ms",
                "source_timestamp": str(ts),
            },
        )
        for ts, price, side, exec_id in ((1, "100", "Buy", "a"), (2, "200", "Sell", "b"))
    ]
    spot = SnapshotRequest("bybit", "spot", "BTCUSDT", "1", 0, 60000)
    result = bridge.research_preview(object(), spot, rows, tick_size="1")
    bar = result["metrics"]["bars"][0]
    assert result["metrics"]["quantity_unit"] == "base"
    assert result["metrics"]["market"]["category"] == "spot"
    assert (bar["vwap"], bar["volume"], bar["delta_known"]) == ("150", "200", "0")
    assert result["f_input"] is None and result["snapshot_admitted"] is False
