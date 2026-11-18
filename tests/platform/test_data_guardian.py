"""Independent positive and negative tests for read-only Data Guardian."""
from copy import deepcopy

from src.api.guardians.data_guardian import reconcile

H = "a" * 64
STEP = 60_000


def snapshot():
    return {
        "environment": "tester_preview", "schema_verified": True,
        "dsn_isolated": True, "postgres_port": 55432,
        "read_only_snapshot": True, "baseline_verified": True,
        "baseline_sha256": H, "category": "linear", "symbol": "BTCUSDT",
        "interval": "1", "start_ms": 0, "end_ms": 3 * STEP,
        "source": "bybit", "source_verified": True,
        "source_gap_proof_verified": True, "catalog_verified": True,
        "catalog_handler": "CatalogHandler", "fenced_writer_verified": True,
        "lease_epoch": 12, "overlapping_writer": False,
        "queue_identity_verified": True,
        "rows": [row(0), row(2 * STEP)],
    }


def row(ts):
    return {
        "open_time_ms": ts, "source": "bybit", "provenance_verified": True,
        "source_sha256": H, "observed_sha256": H,
        "original_projection_verified": True, "open": 2, "high": 3,
        "low": 1, "close": 2, "volume": 0, "closed": True,
    }


def test_disabled_default():
    assert reconcile(snapshot()).reason == "guardians_disabled"


def test_verified_gap_only_proposed():
    result = reconcile(snapshot(), enabled=True)
    assert result.action == "PROPOSE_BOUNDED_BACKFILL"
    assert result.proposal["missing_open_times_ms"] == [STEP]
    assert result.proposal["execution_authorized"] is False
    assert result.proposal["adapter"] == "CatalogHandler"
    assert reconcile(snapshot(), enabled=True).evidence_ref == result.evidence_ref


def test_complete_sample_not_full_history_claim():
    data = snapshot()
    data["rows"].insert(1, row(STEP))
    result = reconcile(data, enabled=True)
    assert result.action == "OBSERVED_CONSISTENT"
    assert "not_full_history" in result.reason


def test_duplicate_quarantined():
    data = snapshot()
    data["rows"].append(row(0))
    assert reconcile(data, enabled=True).action == "PROPOSE_QUARANTINE"


def test_hash_mismatch_quarantined():
    data = snapshot()
    data["rows"][0]["observed_sha256"] = "b" * 64
    assert reconcile(data, enabled=True).reason == "hash_or_projection_conflict"


def test_provenance_mismatch_quarantined():
    data = snapshot()
    data["rows"][0]["source"] = "unknown"
    assert reconcile(data, enabled=True).reason == "source_mismatch"


def test_invalid_ohlc_quarantined():
    data = snapshot()
    data["rows"][0]["high"] = 1
    assert reconcile(data, enabled=True).reason == "invalid_ohlc"


def test_missing_proof_denies_backfill():
    data = snapshot()
    data["source_gap_proof_verified"] = False
    assert reconcile(data, enabled=True).action == "ALERT"


def test_missing_schema_denies():
    data = snapshot()
    data["schema_verified"] = False
    assert reconcile(data, enabled=True).reason == "postgresql_schema_unverified"


def test_cross_database_denies():
    data = snapshot()
    data["postgres_port"] = 5432
    assert reconcile(data, enabled=True).action == "ALERT"
    data["postgres_port"] = 55432
    data["dsn_isolated"] = False
    assert reconcile(data, enabled=True).action == "ALERT"


def test_overlapping_writer_denies():
    data = snapshot()
    data["overlapping_writer"] = True
    assert reconcile(data, enabled=True).reason == "catalog_or_fencing_unverified"


def test_unfenced_denies():
    data = snapshot()
    data["lease_epoch"] = 0
    assert reconcile(data, enabled=True).action == "ALERT"


def test_unclosed_denies():
    data = snapshot()
    data["rows"][0]["closed"] = False
    assert reconcile(data, enabled=True).reason == "unclosed_candle"


def test_window_budget_denies():
    data = snapshot()
    data["end_ms"] = 2 * 86_400_000
    assert reconcile(data, enabled=True).reason == "invalid_bounded_window"


def test_boolean_timestamp_denies():
    data = snapshot()
    data["rows"][0]["open_time_ms"] = True
    assert reconcile(data, enabled=True).reason == "record_out_of_window"


def test_no_input_mutation():
    data = snapshot()
    original = deepcopy(data)
    reconcile(data, enabled=True)
    assert data == original
