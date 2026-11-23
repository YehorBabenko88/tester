"""Independent positive and negative tests for read-only Data Guardian."""

import unittest
from copy import deepcopy

import pytest

from src.api.guardian_policy import decide
from src.api.guardian_supervisor import GuardianSupervisor
from src.api.guardians.data_guardian import (
    assess_data_removal_receipt,
    reconcile,
    to_policy_event,
)

H = "a" * 64
STEP = 60_000


def snapshot():
    return {
        "environment": "tester_preview",
        "schema_verified": True,
        "dsn_isolated": True,
        "postgres_port": 55432,
        "database_name": "tester_preview",
        "database_role": "guardian_readonly",
        "dsn_identity_verified": True,
        "read_only_snapshot": True,
        "baseline_verified": True,
        "independent_integrity_check": True,
        "ownership_verified": True,
        "history_state": "bounded_verified",
        "baseline_sha256": H,
        "category": "linear",
        "symbol": "BTCUSDT",
        "interval": "1",
        "start_ms": 0,
        "end_ms": 3 * STEP,
        "source": "bybit",
        "source_verified": True,
        "source_evidence_sha256": H,
        "source_gap_proof_verified": True,
        "catalog_verified": True,
        "source_gap_proof_sha256": H,
        "source_gap_proof_scope": "bybit/linear/BTCUSDT/1",
        "source_gap_proof_start_ms": 0,
        "source_gap_proof_end_ms": 3 * STEP,
        "catalog_handler": "CatalogHandler",
        "fenced_writer_verified": True,
        "lease_epoch": 12,
        "overlapping_writer": False,
        "lease_scope": "bybit/linear/BTCUSDT/1",
        "lease_evidence_sha256": H,
        "queue_identity_verified": True,
        "rows": [row(0), row(2 * STEP)],
    }


def row(ts):
    return {
        "open_time_ms": ts,
        "close_time_ms": ts + STEP - 1,
        "source": "bybit",
        "provenance_verified": True,
        "category": "linear",
        "symbol": "BTCUSDT",
        "interval": "1",
        "source_sha256": H,
        "observed_sha256": H,
        "original_projection_verified": True,
        "open": 2,
        "high": 3,
        "low": 1,
        "close": 2,
        "volume": 0,
        "closed": True,
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


@pytest.mark.parametrize(
    "field,bad",
    [
        ("database_name", "production"),
        ("database_role", "postgres"),
        ("dsn_identity_verified", False),
        ("postgres_port", True),
        ("postgres_port", "55432"),
        ("postgres_port", 5432),
        ("dsn_isolated", "true"),
    ],
)
def test_forged_dsn_attestation_denied(field, bad):
    data = snapshot()
    data[field] = bad
    assert reconcile(data, enabled=True).reason == "database_isolation_unverified"


@pytest.mark.parametrize(
    "field,bad",
    [
        ("history_state", "unknown"),
        ("history_state", "complete"),
        ("baseline_verified", False),
        ("baseline_sha256", "bad"),
    ],
)
def test_unknown_history_or_baseline_denied(field, bad):
    data = snapshot()
    data[field] = bad
    assert reconcile(data, enabled=True).action == "ALERT"


@pytest.mark.parametrize(
    "field,bad",
    [
        ("source_gap_proof_sha256", "bad"),
        ("source_gap_proof_scope", "bybit/spot/BTCUSDT/1"),
        ("source_gap_proof_start_ms", STEP),
        ("source_gap_proof_end_ms", 4 * STEP),
        ("source_gap_proof_verified", "true"),
    ],
)
def test_forged_or_wrong_scope_gap_proof_denied(field, bad):
    data = snapshot()
    data[field] = bad
    assert reconcile(data, enabled=True).reason == "gap_source_proof_missing"


@pytest.mark.parametrize(
    "field,bad",
    [
        ("lease_scope", "bybit/spot/BTCUSDT/1"),
        ("lease_evidence_sha256", "bad"),
        ("lease_epoch", True),
        ("lease_epoch", -1),
        ("overlapping_writer", None),
        ("fenced_writer_verified", False),
        ("queue_identity_verified", False),
    ],
)
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


def test_fault_injection_corrupt_each_row_without_repair():
    """Corrupt every candle independently; never mutate or authorize repair."""
    for index in (0, 1):
        for field, value in (
            ("open", float("nan")),
            ("high", float("inf")),
            ("low", -float("inf")),
            ("close", -1),
            ("volume", -1),
            ("observed_sha256", "b" * 64),
            ("source", "unknown"),
            ("provenance_verified", False),
            ("original_projection_verified", False),
        ):
            data = snapshot()
            data["rows"][index][field] = value
            original = deepcopy(data)
            result = reconcile(data, enabled=True)
            assert result.action == "PROPOSE_QUARANTINE", (index, field)
            assert result.proposal["execution_authorized"] is False
            assert result.proposal["preserve_raw"] is True            assert data == original


def test_fault_injection_gap_fencing_matrix():
    """A gap alone never grants queue execution or bypasses fencing."""
    for key, value in (
        ("source_gap_proof_verified", False),
        ("source_gap_proof_sha256", None),
        ("source_gap_proof_scope", "bybit/spot/BTCUSDT/1"),
        ("catalog_verified", False),
        ("catalog_handler", "OtherHandler"),
        ("fenced_writer_verified", False),
        ("lease_epoch", 0),
        ("lease_scope", "bybit/spot/BTCUSDT/1"),
        ("lease_evidence_sha256", "invalid"),
        ("overlapping_writer", True),
        ("queue_identity_verified", False),
    ):
        data = snapshot()
        data[key] = value
        assert reconcile(data, enabled=True).action == "ALERT", key


def test_fault_injection_no_implicit_full_history_claim():
    """Even a complete bounded sample cannot certify global history."""
    data = snapshot()
    data["rows"].insert(1, row(STEP))
    result = reconcile(data, enabled=True)
    assert result.action == "OBSERVED_CONSISTENT"
    assert result.proposal is None
    assert result.reason == "sample_consistent_not_full_history"


def test_fault_injection_empty_snapshot_is_not_fabricated():
    data = snapshot()
    data["rows"] = []
    result = reconcile(data, enabled=True)
    assert result.action == "PROPOSE_BOUNDED_BACKFILL"
    assert result.proposal["missing_open_times_ms"] == [0, STEP, 2 * STEP]
    assert result.proposal["execution_authorized"] is False
    assert result.proposal["history_complete"] is False


@pytest.mark.parametrize(
    "field,bad",
    [
        ("source", "synthetic"),
        ("provenance_verified", None),
        ("source_sha256", "z" * 64),
        ("observed_sha256", None),
        ("original_projection_verified", "true"),
    ],
)
def test_fault_injection_untrusted_record_provenance(field, bad):
    data = snapshot()
    data["rows"][1][field] = bad
    result = reconcile(data, enabled=True)
    assert result.action == "PROPOSE_QUARANTINE"
    assert result.proposal["execution_authorized"] is False
    assert result.proposal["preserve_raw"] is True


@pytest.mark.parametrize(
    "field,bad",
    [
        ("schema_verified", "true"),
        ("read_only_snapshot", "true"),
        ("source_verified", "true"),
        ("source_evidence_sha256", "unknown"),
    ],
)
def test_fault_injection_forged_reader_attestation(field, bad):
    data = snapshot()
    data[field] = bad
    assert reconcile(data, enabled=True).action == "ALERT"


def test_fault_injection_duplicate_identity_has_stable_evidence():
    data = snapshot()
    data["rows"].append(row(0))
    first = reconcile(data, enabled=True)
    second = reconcile(deepcopy(data), enabled=True)
    assert first.action == second.action == "PROPOSE_QUARANTINE"
    assert first.evidence_ref == second.evidence_ref
    assert first.proposal["execution_authorized"] is False


@pytest.mark.parametrize(
    "field,bad",
    [
        ("category", "spot"),
        ("symbol", "ETHUSDT"),
        ("interval", "5"),
    ],
)
def test_cross_scope_row_provenance_quarantined(field, bad):
    data = snapshot()
    data["rows"][0][field] = bad
    result = reconcile(data, enabled=True)
    assert result.action == "PROPOSE_QUARANTINE"
    assert result.reason == "source_mismatch"
    assert result.proposal["execution_authorized"] is False
    assert result.proposal["preserve_raw"] is True


@pytest.mark.parametrize("field", ["category", "symbol", "interval"])
def test_missing_explicit_row_identity_quarantined(field):
    data = snapshot()
    del data["rows"][0][field]
    result = reconcile(data, enabled=True)
    assert result.action == "PROPOSE_QUARANTINE"
    assert result.reason == "source_mismatch"
    assert result.proposal["execution_authorized"] is False
    assert result.proposal["preserve_raw"] is True


@pytest.mark.parametrize("bad", [None, 0, STEP, STEP - 2, True, "59999"])
def test_closed_candle_end_timestamp_fault_injection(bad):
    data = snapshot()
    data["rows"][0]["close_time_ms"] = bad
    result = reconcile(data, enabled=True)
    assert result.action == "PROPOSE_QUARANTINE"
    assert result.reason == "candle_close_time_mismatch"
    assert result.proposal["execution_authorized"] is False
    assert result.proposal["preserve_raw"] is True


@pytest.mark.parametrize(
    "field,bad",
    [
        ("schema_verified", False),
        ("baseline_verified", False),
        ("dsn_isolated", False),
        ("database_name", "postgres"),
        ("database_role", "postgres"),
        ("postgres_port", 5432),
        ("read_only_snapshot", False),
    ],
)
def test_reinstall_readonly_preflight_fails_closed(field, bad):
    """No data repair or installation authorization follows from bad inventory."""
    data = snapshot()
    data[field] = bad
    original = deepcopy(data)
    result = reconcile(data, enabled=True)
    assert result.action == "ALERT"
    assert result.proposal is None
    assert data == original


def test_reinstall_preflight_never_authorizes_effects():
    data = snapshot()
    for rows in ([], [row(0), row(2 * STEP)], [row(0), row(0)]):
        candidate = deepcopy(data)
        candidate["rows"] = rows
        finding = reconcile(candidate, enabled=True)
        assert finding.action in {"PROPOSE_BOUNDED_BACKFILL", "PROPOSE_QUARANTINE"}
        assert finding.proposal["execution_authorized"] is False
        assert finding.proposal["preserve_raw"] is True


@pytest.mark.parametrize(
    "port,role,dbname",
    [
        (5432, "guardian_readonly", "tester_preview"),
        (55432, "postgres", "tester_preview"),
        (55432, "guardian_readonly", "postgres"),
        (None, "guardian_readonly", "tester_preview"),
    ],
)
def test_destructive_contract_foreign_cluster_never_authorized(port, role, dbname):
    """A foreign or unknown cluster cannot yield any data mutation capability."""
    data = snapshot()
    data["postgres_port"] = port
    data["database_role"] = role
    data["database_name"] = dbname
    result = reconcile(data, enabled=True)
    assert result.action == "ALERT"
    assert result.proposal is None


def test_destructive_contract_no_delete_capability():
    """Even trusted data evidence never grants an uninstall or deletion."""
    for rows in ([], [row(0), row(2 * STEP)], [row(0), row(0)]):
        data = snapshot()
        data["rows"] = rows
        result = reconcile(data, enabled=True)
        assert result.action not in {"DELETE", "DROP_DATABASE", "UNINSTALL"}
        assert result.proposal is not None
        assert result.proposal["execution_authorized"] is False
        assert result.proposal["preserve_raw"] is True



def data_removal_fixture():
    evidence = {
        "environment": "disposable_windows_fixture",
        "master_gate_verified": True,
        "master_gate_receipt_sha256": H,
        "independent_data_readback_verified": True,
        "dedicated_cluster_identity_verified": True,
        "system_pg_identity_unchanged": True,
        "system_pg_data_hash_unchanged": True,
        "data_receipt_sha256": H,
    }
    for key in (
        "dedicated_cluster_absent",
        "raw_candles_absent",
        "history_absent",
        "original_projections_absent",
        "provenance_absent",
        "source_hashes_absent",
        "test_results_absent",
        "checkpoints_absent",
        "download_queue_absent",
        "data_backups_absent",
    ):
        evidence[key] = True
    return evidence


def test_data_removal_disabled_default():
    assert assess_data_removal_receipt(data_removal_fixture()).reason == ("guardians_disabled")


def test_data_removal_is_fixture_only_not_an_uninstall():
    result = assess_data_removal_receipt(data_removal_fixture(), enabled=True)
    assert result.action == "OBSERVED_DATA_REMOVAL_FIXTURE"
    assert result.proposal is None


@pytest.mark.parametrize(
    "field,bad",
    [
        ("environment", "tester_preview"),
        ("master_gate_verified", False),
        ("master_gate_receipt_sha256", "fake"),
        ("independent_data_readback_verified", False),
        ("dedicated_cluster_identity_verified", False),
        ("system_pg_identity_unchanged", False),
        ("system_pg_data_hash_unchanged", False),
        ("data_receipt_sha256", None),
    ],
)
def test_data_removal_rejects_foreign_or_unsigned_receipt(field, bad):
    evidence = data_removal_fixture()
    evidence[field] = bad
    assert assess_data_removal_receipt(evidence, enabled=True).action == "ALERT"


@pytest.mark.parametrize(
    "missing",
    [
        "dedicated_cluster_absent",
        "raw_candles_absent",
        "history_absent",
        "original_projections_absent",
        "provenance_absent",
        "source_hashes_absent",
        "test_results_absent",
        "checkpoints_absent",
        "download_queue_absent",
        "data_backups_absent",
    ],
)
def test_data_removal_rejects_partial_deletion(missing):
    evidence = data_removal_fixture()
    evidence[missing] = False
    assert assess_data_removal_receipt(evidence, enabled=True).reason == (
        "data_removal_incomplete_or_unverified"
    )


def test_data_removal_does_not_touch_evidence():
    evidence = data_removal_fixture()
    original = deepcopy(evidence)
    assess_data_removal_receipt(evidence, enabled=True)
    assert evidence == original


def test_independent_integrity_gate_rejects_unverified_corruption():
    data = snapshot()
    data["independent_integrity_check"] = False
    data["rows"][0]["high"] = float("nan")
    result = reconcile(data, enabled=True)
    assert result.action == "ALERT"
    assert result.reason == "integrity_evidence_not_verified"
    assert result.proposal is None


def test_distinct_candle_conflicts_receive_distinct_digests():
    a = snapshot()
    b = snapshot()
    a["rows"][0]["volume"] = -1
    b["rows"][1]["volume"] = -1
    x = reconcile(a, enabled=True)
    y = reconcile(b, enabled=True)
    assert x.action == y.action == "PROPOSE_QUARANTINE"
    assert x.evidence_ref != y.evidence_ref
    assert x.proposal["affected_open_time_ms"] == 0
    assert y.proposal["affected_open_time_ms"] == 2 * STEP


def test_foundation_policy_event_gap_integration():
    data = snapshot()
    finding = reconcile(data, enabled=True)
    event = to_policy_event(data, finding)
    assert event is not None
    assert event["signal"] == "verified_gap"
    assert decide(event).action == "PROPOSE_BOUNDED_BACKFILL"
    proposal = GuardianSupervisor(enabled=True).propose(event)
    assert proposal.allowed is True
    assert proposal.action == "PROPOSE_BOUNDED_BACKFILL"


def test_foundation_policy_event_quarantine_integration():
    data = snapshot()
    data["rows"][0]["source"] = "unknown"
    finding = reconcile(data, enabled=True)
    event = to_policy_event(data, finding)
    assert event is not None
    assert event["signal"] == "source_mismatch"
    assert decide(event).action == "PROPOSE_QUARANTINE"
    proposal = GuardianSupervisor(enabled=True).propose(event)
    assert proposal.allowed is True
    assert proposal.action == "PROPOSE_QUARANTINE"


@pytest.mark.parametrize(
    "field,bad",
    [
        ("ownership_verified", False),
        ("source_evidence_sha256", None),
        ("catalog_verified", False),
    ],
)
def test_forged_gap_supervisor_event_denied(field, bad):
    data = snapshot()
    finding = reconcile(data, enabled=True)
    data[field] = bad
    event = to_policy_event(data, finding)
    # A stored finding is never a reusable capability after evidence changes.
    assert event is None


def test_nonproposal_cannot_be_promoted_to_supervisor():
    data = snapshot()
    data["baseline_verified"] = False
    finding = reconcile(data, enabled=True)
    assert to_policy_event(data, finding) is None


def test_data_event_never_directly_authorizes_execution():
    data = snapshot()
    event = to_policy_event(data, reconcile(data, enabled=True))
    assert event is not None
    assert "execution_authorized" not in event
    assert "SQL" not in repr(event)


def test_forged_finding_is_not_a_supervisor_capability():
    data = snapshot()
    actual = reconcile(data, enabled=True)
    from src.api.guardians.data_guardian import Finding

    forged = Finding(
        action="PROPOSE_BOUNDED_BACKFILL",
        reason=actual.reason,
        scope=actual.scope,
        evidence_ref=actual.evidence_ref,
        proposal={"adapter": "CatalogHandler", "execution_authorized": False},
    )
    assert to_policy_event(data, forged) is None


def test_revoked_lease_invalidates_existing_finding():
    data = snapshot()
    prior = reconcile(data, enabled=True)
    data["overlapping_writer"] = True
    assert to_policy_event(data, prior) is None


def test_revoked_source_proof_invalidates_existing_finding():
    data = snapshot()
    prior = reconcile(data, enabled=True)
    data["source_gap_proof_verified"] = False
    assert to_policy_event(data, prior) is None


@pytest.mark.parametrize(
    "field",
    [
        "source_gap_proof_sha256",
        "source_evidence_sha256",
        "lease_evidence_sha256",
    ],
)
def test_changed_verified_gap_proof_rotates_receipt(field):
    original = snapshot()
    old = reconcile(original, enabled=True)
    updated = deepcopy(original)
    updated[field] = "b" * 64
    new = reconcile(updated, enabled=True)
    assert old.action == new.action == "PROPOSE_BOUNDED_BACKFILL"
    assert old.evidence_ref != new.evidence_ref
    assert to_policy_event(updated, old) is None


def test_lease_epoch_rotation_rotates_gap_receipt():
    original = snapshot()
    old = reconcile(original, enabled=True)
    updated = deepcopy(original)
    updated["lease_epoch"] += 1
    new = reconcile(updated, enabled=True)
    assert old.evidence_ref != new.evidence_ref
    assert to_policy_event(updated, old) is None


def test_same_candle_different_corruption_changes_quarantine_digest():
    a, b = snapshot(), snapshot()
    a["rows"][0]["high"] = 0
    b["rows"][0]["high"] = -3
    old = reconcile(a, enabled=True)
    new = reconcile(b, enabled=True)
    assert old.action == new.action == "PROPOSE_QUARANTINE"
    assert old.reason == new.reason == "invalid_ohlc"
    assert old.evidence_ref != new.evidence_ref
    assert to_policy_event(b, old) is None


def test_unbounded_record_identity_fails_closed():
    item = snapshot()
    item["rows"][0]["high"] = 10**5000
    result = reconcile(item, enabled=True)
    assert result.action == "ALERT"
    assert result.reason == "invalid_conflict_record_identity"
    assert result.proposal is None


def test_nested_record_identity_fails_closed():
    item = snapshot()
    item["rows"][0]["high"] = {"not": "a scalar"}
    result = reconcile(item, enabled=True)
    assert result.action == "ALERT"
    assert result.reason == "invalid_conflict_record_identity"
    assert result.proposal is None


def test_fault_injection_partial_batch_never_confirms_full_history():
    """A truncated reader result can only propose missing identities."""
    baseline = snapshot()
    baseline["rows"].insert(1, row(STEP))
    full = reconcile(baseline, enabled=True)
    assert full.action == "OBSERVED_CONSISTENT"
    truncated = deepcopy(baseline)
    del truncated["rows"][1:]
    partial = reconcile(truncated, enabled=True)
    assert partial.action == "PROPOSE_BOUNDED_BACKFILL"
    assert partial.proposal["missing_open_times_ms"] == [STEP, 2 * STEP]
    assert partial.proposal["history_complete"] is False
    assert partial.proposal["execution_authorized"] is False
    assert baseline["rows"] == [row(0), row(STEP), row(2 * STEP)]


def test_fault_injection_interrupted_read_preserves_confirmed_input():
    """A failed read attestation must not mutate a previously verified batch."""
    confirmed = snapshot()
    confirmed["rows"].insert(1, row(STEP))
    preserved = deepcopy(confirmed)
    aborted = deepcopy(confirmed)
    aborted["read_only_snapshot"] = False
    aborted["rows"] = [row(0)]
    denied = reconcile(aborted, enabled=True)
    assert denied.action == "ALERT"
    assert denied.reason == "snapshot_not_read_only"
    assert denied.proposal is None
    assert confirmed == preserved
    assert reconcile(confirmed, enabled=True).action == "OBSERVED_CONSISTENT"


def test_fault_injection_restart_replay_does_not_execute_or_repair():
    """Pure restart/replay has stable findings and no persistent side effects."""
    source = snapshot()
    before = deepcopy(source)
    initial = reconcile(source, enabled=True)
    after_restart = reconcile(deepcopy(source), enabled=True)
    assert initial == after_restart
    assert initial.action == "PROPOSE_BOUNDED_BACKFILL"
    assert initial.proposal["execution_authorized"] is False
    assert source == before


def test_fault_injection_stale_lease_after_restart_denies_backfill():
    source = snapshot()
    prior = reconcile(source, enabled=True)
    restarted = deepcopy(source)
    restarted["lease_epoch"] = 0
    assert reconcile(restarted, enabled=True).action == "ALERT"
    assert to_policy_event(restarted, prior) is None


def test_fault_injection_corrupt_baseline_after_restart_denies_all_actions():
    source = snapshot()
    prior = reconcile(source, enabled=True)
    restarted = deepcopy(source)
    restarted["baseline_sha256"] = "corrupted"
    result = reconcile(restarted, enabled=True)
    assert result.action == "ALERT"
    assert result.proposal is None
    assert to_policy_event(restarted, prior) is None


def test_fault_injection_foreign_database_and_pg5432_denied_after_restart():
    source = snapshot()
    prior = reconcile(source, enabled=True)
    for database, port in (("postgres", 55432), ("tester_preview", 5432)):
        restarted = deepcopy(source)
        restarted["database_name"] = database
        restarted["postgres_port"] = port
        assert reconcile(restarted, enabled=True).action == "ALERT"
        assert to_policy_event(restarted, prior) is None


def test_fault_injection_partial_uninstall_never_reports_data_removed():
    source = data_removal_fixture()
    for field in ("raw_candles_absent", "provenance_absent", "test_results_absent"):
        damaged = deepcopy(source)
        damaged[field] = False
        finding = assess_data_removal_receipt(damaged, enabled=True)
        assert finding.action == "ALERT"
        assert finding.proposal is None
    assert source == data_removal_fixture()


def test_disposable_sqlite_partial_write_rollback_preserves_confirmed_rows(tmp_path):
    """SQLite-only transaction fault; NOT PostgreSQL crash acceptance."""
    import sqlite3

    db = tmp_path / "data-guardian-disposable.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE candles (open_ms INTEGER PRIMARY KEY, source_sha TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO candles VALUES (?, ?)", (0, H))
    connection = sqlite3.connect(db)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT INTO candles VALUES (?, ?)", (STEP, "b" * 64))
        connection.rollback()  # injected failure before commit
    finally:
        connection.close()
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT open_ms, source_sha FROM candles ORDER BY open_ms"
        ).fetchall() == [(0, H)]


def test_disposable_sqlite_commit_survives_reader_restart(tmp_path):
    """SQLite-only restart/ack loss, with duplicate-key protection."""
    import sqlite3

    db = tmp_path / "data-guardian-disposable.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE candles (open_ms INTEGER PRIMARY KEY, source_sha TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO candles VALUES (?, ?)", (0, H))
        connection.execute("INSERT INTO candles VALUES (?, ?)", (STEP, "b" * 64))
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT open_ms, source_sha FROM candles ORDER BY open_ms"
        ).fetchall() == [(0, H), (STEP, "b" * 64)]
        try:
            connection.execute("INSERT INTO candles VALUES (?, ?)", (STEP, "c" * 64))
        except sqlite3.IntegrityError:
            connection.rollback()
        else:
            pytest.fail("duplicate candle key was accepted")
        assert connection.execute(
            "SELECT source_sha FROM candles WHERE open_ms=?", (STEP,)
        ).fetchone() == ("b" * 64,)


def test_disposable_sqlite_connection_drop_rolls_back_pending_candle(tmp_path):
    """Uncommitted insert must not overwrite previously confirmed history."""
    import sqlite3

    db = tmp_path / "data-guardian-disposable.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE candles (open_ms INTEGER PRIMARY KEY, source_sha TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO candles VALUES (?, ?)", (0, H))
    connection = sqlite3.connect(db)
    connection.execute("INSERT INTO candles VALUES (?, ?)", (STEP, "b" * 64))
    connection.close()  # simulate lost connection before COMMIT
    with sqlite3.connect(db) as reopened:
        assert reopened.execute("SELECT open_ms, source_sha FROM candles").fetchall() == [(0, H)]


def test_disposable_sqlite_failed_duplicate_batch_is_atomic(tmp_path):
    """A batch with a duplicate key cannot partially replace confirmed rows."""
    import sqlite3

    db = tmp_path / "data-guardian-atomic.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE candles (open_ms INTEGER PRIMARY KEY, source_sha TEXT NOT NULL)")
        conn.execute("INSERT INTO candles VALUES (?, ?)", (0, H))
    with sqlite3.connect(db) as conn:
        try:
            conn.execute("INSERT INTO candles VALUES (?, ?)", (STEP, "b" * 64))
            conn.execute("INSERT INTO candles VALUES (?, ?)", (0, "c" * 64))
        except sqlite3.IntegrityError:
            conn.rollback()
        else:
            pytest.fail("conflicting batch unexpectedly committed")
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT open_ms, source_sha FROM candles ORDER BY open_ms"
        ).fetchall() == [(0, H)]


def test_disposable_sqlite_bad_candle_rejected_before_write(tmp_path):
    """A corrupt OHLCV row must never enter even a disposable database."""
    import sqlite3

    db = tmp_path / "data-guardian-reject.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE candles (open_ms INTEGER PRIMARY KEY, source_sha TEXT NOT NULL)")
        conn.execute("INSERT INTO candles VALUES (?, ?)", (0, H))
    incoming = snapshot()
    incoming["rows"][0]["high"] = float("nan")
    finding = reconcile(incoming, enabled=True)
    assert finding.action == "PROPOSE_QUARANTINE"
    assert finding.proposal["execution_authorized"] is False
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT open_ms, source_sha FROM candles ORDER BY open_ms"
        ).fetchall() == [(0, H)]


def test_disposable_sqlite_foreign_database_remains_unchanged(tmp_path):
    """Fixture-only proof that unrelated SQLite DB is outside TESTER scope."""
    import sqlite3

    owned = tmp_path / "owned.sqlite3"
    foreign = tmp_path / "foreign.sqlite3"
    for path, stamp in ((owned, "owned"), (foreign, "foreign")):
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE marker (value TEXT NOT NULL)")
            conn.execute("INSERT INTO marker VALUES (?)", (stamp,))
    with sqlite3.connect(owned) as conn:
        conn.execute("DELETE FROM marker")
    with sqlite3.connect(foreign) as conn:
        assert conn.execute("SELECT value FROM marker").fetchall() == [("foreign",)]
    assert foreign.exists()


@pytest.mark.parametrize(
    "field,bad",
    [
        ("open_time_ms", None),
        ("open_time_ms", STEP // 2),
        ("open_time_ms", -STEP),
        ("close_time_ms", STEP - 2),
        ("closed", False),
        ("provenance_verified", False),
        ("source_sha256", None),
        ("observed_sha256", "b" * 64),
    ],
)
def test_restarted_reader_rejects_corrupt_candle_without_mutating_baseline(field, bad):
    confirmed = snapshot()
    confirmed["rows"].insert(1, row(STEP))
    preserved = deepcopy(confirmed)
    incoming = deepcopy(confirmed)
    incoming["rows"][1][field] = bad
    finding = reconcile(incoming, enabled=True)
    assert finding.action in {"ALERT", "PROPOSE_QUARANTINE"}
    assert finding.proposal is None or (
        finding.proposal["execution_authorized"] is False
        and finding.proposal["preserve_raw"] is True
    )
    assert confirmed == preserved
    assert reconcile(confirmed, enabled=True).action == "OBSERVED_CONSISTENT"


def test_data_removal_receipt_requires_all_data_categories_and_no_backups():
    evidence = data_removal_fixture()
    for field in (
        "raw_candles_absent",
        "history_absent",
        "original_projections_absent",
        "provenance_absent",
        "source_hashes_absent",
        "test_results_absent",
        "checkpoints_absent",
        "download_queue_absent",
        "data_backups_absent",
        "dedicated_cluster_absent",
    ):
        missing = deepcopy(evidence)
        missing.pop(field)
        assert assess_data_removal_receipt(missing, enabled=True).action == "ALERT"
    assert assess_data_removal_receipt(evidence, enabled=True).action == (
        "OBSERVED_DATA_REMOVAL_FIXTURE"
    )


class DataGuardianUnittestAcceptance(unittest.TestCase):
    """CI unittest-discover bridge for P0 Data fail-closed invariants."""

    def test_no_actual_io_imports_in_pure_data_guardian(self):
        """Protect against accidental DB/installer/HTTP effectors in Data module."""
        import ast
        import inspect

        from src.api.guardians import data_guardian

        tree = ast.parse(inspect.getsource(data_guardian))
        imported = {
            name.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for name in node.names
        }
        imported.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertFalse(
            imported.intersection(
                {"psycopg", "sqlite3", "subprocess", "requests", "httpx", "winreg"}
            )
        )

    def test_default_disabled_and_pg5432_denied(self):
        self.assertEqual(reconcile(snapshot()).reason, "guardians_disabled")
        sample = snapshot()
        sample["postgres_port"] = 5432
        self.assertEqual(reconcile(sample, enabled=True).action, "ALERT")

    def test_ohlcv_provenance_duplicate_and_gap(self):
        sample = snapshot()
        self.assertEqual(reconcile(sample, enabled=True).action, "PROPOSE_BOUNDED_BACKFILL")
        sample["rows"][0]["high"] = float("nan")
        self.assertEqual(reconcile(sample, enabled=True).action, "PROPOSE_QUARANTINE")
        sample = snapshot()
        sample["rows"][0]["provenance_verified"] = False
        self.assertEqual(reconcile(sample, enabled=True).action, "PROPOSE_QUARANTINE")
        sample = snapshot()
        sample["rows"].append(row(0))
        self.assertEqual(reconcile(sample, enabled=True).reason, "duplicate_key")

    def test_restart_replay_and_stale_lease(self):
        sample = snapshot()
        first = reconcile(sample, enabled=True)
        self.assertEqual(first, reconcile(deepcopy(sample), enabled=True))
        restarted = deepcopy(sample)
        restarted["lease_epoch"] = 0
        self.assertIsNone(to_policy_event(restarted, first))

    def test_disposable_sqlite_rollback_and_preserved_baseline(self):
        import sqlite3

        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE TABLE candles (stamp INTEGER PRIMARY KEY)")
            connection.execute("INSERT INTO candles VALUES (0)")
            connection.commit()
            connection.execute("INSERT INTO candles VALUES (?)", (STEP,))
            connection.rollback()
            self.assertEqual(connection.execute("SELECT stamp FROM candles").fetchall(), [(0,)])
        finally:
            connection.close()

    def test_data_only_removal_requires_protected_pg_and_no_backups(self):
        evidence = data_removal_fixture()
        self.assertEqual(
            assess_data_removal_receipt(evidence, enabled=True).action,
            "OBSERVED_DATA_REMOVAL_FIXTURE",
        )
        evidence["system_pg_data_hash_unchanged"] = False
        self.assertEqual(assess_data_removal_receipt(evidence, enabled=True).action, "ALERT")
        evidence["system_pg_data_hash_unchanged"] = True
        evidence["data_backups_absent"] = False
        self.assertEqual(assess_data_removal_receipt(evidence, enabled=True).action, "ALERT")
