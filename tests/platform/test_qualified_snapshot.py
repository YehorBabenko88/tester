"""Pure bounded snapshot/provenance admission tests; never connect to PostgreSQL."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from src.integration.qualified_snapshot import (
    SnapshotRequest,
    certified_fixture_snapshot,
    read_canonical_snapshot,
    verify_qualified_snapshot,
)


def request(**changes):
    return SnapshotRequest(
        **dict(
            venue="bybit",
            category="linear",
            symbol="BTCUSDT",
            interval="1",
            start_ms=0,
            end_ms=120000,
            **changes,
        )
    )


def page():
    return dict(
        outcome="OK",
        scope=dict(
            venue="bybit", category="linear", symbol="BTCUSDT", dataset="candles", interval="1"
        ),
        rows=[
            dict(
                timestamp_ms=0,
                timestamp_ns=None,
                timestamp_precision="ms",
                payload=dict(open="100.000000000000000001", close="101"),
            )
        ],
        observations=[dict(source_id="source", run_id="caller-run")],
        run_links=[],
        evidence=[dict(evidence_id="proof", verified=True)],
        page_exhausted=True,
        next_cursor=None,
        coverage=dict(
            start_ms=0,
            end_ms=120000,
            complete=True,
            state="COMPLETE",
            proof_refs=["proof"],
            reasons=[],
        ),
        knowledge_time=dict(
            status="UNAVAILABLE",
            quality_available_at_ms=None,
            historical_as_of=False,
            reasons=["historical_visibility_not_proven"],
        ),
        snapshot=dict(isolation="repeatable read", clock_ms=1000000),
    )


def test_pinned_fixture_is_explicit_synthetic_immutable_and_existing_f_shape():
    value = certified_fixture_snapshot()
    assert value.mode == "SYNTHETIC" and value.synthetic and value.admitted
    assert value.certification == "SYNTHETIC_ONLY"
    assert value.snapshot_id == "synthetic:" + value.snapshot_digest
    assert value.snapshot["candles"][0]["open"] == "100"
    assert value.snapshot["provenance"][0]["source_kind"] == "synthetic_fixture"
    copy = value.snapshot
    copy["candles"][0]["open"] = "999"
    assert value.snapshot["candles"][0]["open"] == "100"
    assert verify_qualified_snapshot(value.as_dict()).as_dict() == value.as_dict()


@pytest.mark.parametrize(
    "field,value", [("mode", "REAL"), ("admitted", False), ("dataset_revision", "0" * 64)]
)
def test_tampered_or_relabeled_fixture_cannot_be_certified(field, value):
    document = certified_fixture_snapshot().as_dict()
    document[field] = value
    with pytest.raises(ValueError):
        verify_qualified_snapshot(document)


def test_caller_rehashed_synthetic_snapshot_is_not_pinned_fixture():
    from src.strategy.dsl import digest

    document = certified_fixture_snapshot().as_dict()
    document["snapshot"]["candles"][0]["open"] = "100.5"
    document["snapshot_digest"] = digest(document["snapshot"])
    document["snapshot_id"] = "synthetic:" + document["snapshot_digest"]
    with pytest.raises(ValueError):
        verify_qualified_snapshot(document)


def test_real_complete_cannot_manufacture_historical_knowledge_or_f_input():
    source = page()
    store = Mock()
    store.read_page.return_value = source
    value = read_canonical_snapshot(store, request())
    assert value.mode == "REAL" and not value.admitted and not value.synthetic
    assert value.certification == "UNVERIFIED" and value.snapshot is None
    assert "historical_visibility_not_proven" in value.reasons
    assert (
        value.provenance["canonical_page"]["rows"][0]["payload"]["open"] == "100.000000000000000001"
    )
    assert value.provenance["canonical_page"]["knowledge_time"] == source["knowledge_time"]
    assert store.read_page.call_count == 1
    assert store.read_page.call_args.kwargs["limit"] == 1000
    assert source == page()


@pytest.mark.parametrize(
    "change,reason",
    [
        (
            {"coverage": dict(complete=False, state="UNKNOWN", proof_refs=[], reasons=[])},
            "coverage_unknown",
        ),
        ({"page_exhausted": False, "next_cursor": [1, "a" * 64]}, "single_page_limit"),
        ({"outcome": "RESOURCE_EXHAUSTED"}, "canonical_read_unavailable"),
        ({"rows": []}, "no_price_evidence"),
    ],
)
def test_unknown_exhausted_or_empty_canonical_read_stays_blocked(change, reason):
    response = page()
    response.update(deepcopy(change))
    store = Mock()
    store.read_page.return_value = response
    result = read_canonical_snapshot(store, request())
    assert not result.admitted and result.snapshot is None and reason in result.reasons


def test_revision_stable_across_db_read_time_and_rejects_wrong_expected_revision():
    store = Mock()
    store.read_page.return_value = page()
    first = read_canonical_snapshot(store, request())
    store.read_page.return_value["snapshot"]["clock_ms"] += 5000
    assert read_canonical_snapshot(store, request()).dataset_revision == first.dataset_revision
    with pytest.raises(ValueError, match="revision"):
        read_canonical_snapshot(store, request(dataset_revision="0" * 64))


@pytest.mark.parametrize(
    "changes",
    [
        {"category": "inverse"},
        {"interval": "M"},
        {"end_ms": 60000 * 1001},
        {"symbol": "../private"},
    ],
)
def test_request_bounds_before_database(changes):
    values = dict(
        venue="bybit", category="linear", symbol="BTCUSDT", interval="1", start_ms=0, end_ms=120000
    )
    values.update(changes)
    with pytest.raises(ValueError):
        SnapshotRequest(**values)
