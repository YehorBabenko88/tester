"""Independent positive and negative tests for read-only Data Guardian."""
from copy import deepcopy

import pytest

from src.api.guardians.data_guardian import reconcile

H = "a" * 64
STEP = 60_000


def snapshot():
    return {
        "environment": "tester_preview", "schema_verified": True,
        "dsn_isolated": True, "postgres_port": 55432,
        "database_name": "tester_preview", "database_role": "guardian_readonly",
        "dsn_identity_verified": True,
        "read_only_snapshot": True, "baseline_verified": True,
        "history_state": "bounded_verified",
        "baseline_sha256": H, "category": "linear", "symbol": "BTCUSDT",
        "interval": "1", "start_ms": 0, "end_ms": 3 * STEP,
        "source": "bybit", "source_verified": True,
        "source_evidence_sha256": H,
        "source_gap_proof_verified": True, "catalog_verified": True,
        "source_gap_proof_sha256": H,
        "source_gap_proof_scope": "bybit/linear/BTCUSDT/1",
        "source_gap_proof_start_ms": 0, "source_gap_proof_end_ms": 3 * STEP,
        "catalog_handler": "CatalogHandler", "fenced_writer_verified": True,
        "lease_epoch": 12, "overlapping_writer": False,
        "lease_scope": "bybit/linear/BTCUSDT/1",
        "lease_evidence_sha256": H,
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


@pytest.mark.parametrize("field", ["open", "high", "low", "close"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), -1, 0, True])
def test_invalid_price_quarantine(field, bad):
    data = snapshot()
    data["rows"][0][field] = bad
    result = reconcile(data, enabled=True)
    assert result.action == "PROPOSE_QUARANTINE"
    assert result.reason == "invalid_ohlc"
    assert result.proposal["preserve_raw"] is True
    assert result.proposal["execution_authorized"] is False


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), -1, True, "0"])
def test_invalid_volume_quarantine(bad):
    data = snapshot()
    data["rows"][0]["volume"] = bad
    assert reconcile(data, enabled=True).reason == "invalid_volume"


@pytest.mark.parametrize("field,bad", [
    ("database_name", "production"), ("database_role", "postgres"),
    ("dsn_identity_verified", False), ("postgres_port", True),
    ("postgres_port", "55432"), ("postgres_port", 5432),
    ("dsn_isolated", "true"),
])
def test_forged_dsn_attestation_denied(field, bad):
    data = snapshot()
    data[field] = bad
    assert reconcile(data, enabled=True).reason == "database_isolation_unverified"


@pytest.mark.parametrize("field,bad", [
    ("history_state", "unknown"), ("history_state", "complete"),
    ("baseline_verified", False), ("baseline_sha256", "bad"),
])
def test_unknown_history_or_baseline_denied(field, bad):
    data = snapshot()
    data[field] = bad
    assert reconcile(data, enabled=True).action == "ALERT"


@pytest.mark.parametrize("field,bad", [
    ("source_gap_proof_sha256", "bad"),
    ("source_gap_proof_scope", "bybit/spot/BTCUSDT/1"),
    ("source_gap_proof_start_ms", STEP),
    ("source_gap_proof_end_ms", 4 * STEP),
    ("source_gap_proof_verified", "true"),
])
def test_forged_or_wrong_scope_gap_proof_denied(field, bad):
    data = snapshot()
    data[field] = bad
    assert reconcile(data, enabled=True).reason == "gap_source_proof_missing"


@pytest.mark.parametrize("field,bad", [
    ("lease_scope", "bybit/spot/BTCUSDT/1"),
    ("lease_evidence_sha256", "bad"), ("lease_epoch", True),
    ("lease_epoch", -1), ("overlapping_writer", None),
    ("fenced_writer_verified", False), ("queue_identity_verified", False),
])
def test_fencing_and_overlap_denied(field, bad):
    data = snapshot()
    data[field] = bad
    assert reconcile(data, enabled=True).reason == "catalog_or_fencing_unverified"


def test_noncontiguous_missing_windows_proposal_only():
    data = snapshot()
    data["end_ms"] = 5 * STEP
    data["source_gap_proof_end_ms"] = 5 * STEP
    data["rows"] = [row(0), row(2 * STEP), row(4 * STEP)]
    result = reconcile(data, enabled=True)
    assert result.action == "PROPOSE_BOUNDED_BACKFILL"
    assert result.proposal["missing_open_times_ms"] == [STEP, 3 * STEP]
    assert result.proposal["history_complete"] is False
    assert result.proposal["execution_authorized"] is False


def test_conflict_evidence_preserved_without_mutation():
    data = snapshot()
    data["rows"].append(row(0))
    before = deepcopy(data)
    result = reconcile(data, enabled=True)
    assert result.action == "PROPOSE_QUARANTINE"
    assert result.proposal["preserve_raw"] is True
    assert result.proposal["execution_authorized"] is False
    assert data == before


def test_source_evidence_missing_denied():
    data = snapshot()
    data["source_evidence_sha256"] = None
    assert reconcile(data, enabled=True).reason == "source_unverified"


@pytest.mark.parametrize("bad", [[], {}, 123, None])
def test_malformed_interval_fails_closed(bad):
    data = snapshot()
    data["interval"] = bad
    assert reconcile(data, enabled=True).action == "ALERT"


@pytest.mark.parametrize("field", ["open", "volume"])
def test_extremely_large_integer_fails_closed(field):
    data = snapshot()
    data["rows"][0][field] = 10**1000
    result = reconcile(data, enabled=True)
    assert result.action == "PROPOSE_QUARANTINE"
    assert result.proposal["execution_authorized"] is False
