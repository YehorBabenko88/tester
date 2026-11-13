"""Bounded DATA #72 execution source readback is evidence, never a chart grant."""

from types import SimpleNamespace

from src.api.capability_data_bridge import observed_execution_evidence
from src.api.models import Query


def query(**update):
    values = dict(category="linear", symbol="BTCUSDT", interval="1",
                  start_ms=0, end_ms=60000)
    values.update(update)
    return Query(**values)


def event(**update):
    values = dict(
        contract_version="tester-public-execution/1",
        venue="bybit",
        category="linear",
        symbol="BTCUSDT",
        exec_id="original-1",
        taker_side="Unknown",
        price="100.0000000000000000001",
        quantity="0.000000000000000001",
        quantity_unit="source_native_linear_size",
        event_ms=1000,
        observed_ms=5000,
        publication_ms=None,
        utc_interval=dict(start_ms=0, end_ms=60000),
        source_coverage=dict(status="UNKNOWN", start_ms=0, end_ms=60000),
        availability=dict(status="UNVERIFIED", reason="Historical visibility unknown"),
        source_receipts=[
            dict(evidence_id="ledger-1", page_ref="page:1",
                 http_sha256="a" * 64, page_sha256="b" * 64,
                 row_sha256="c" * 64)
        ],
        provenance=[
            dict(source_kind="bybit_rest",
                 source_ref="https://api.bybit.com/v5/market/recent-trade?"
                            "category=linear&symbol=BTCUSDT&limit=100",
                 source_hash="c" * 64, timestamp_precision="ms",
                 source_timestamp="1000", ingestion_run="receipt-run")
        ],
    )
    values.update(update)
    return values


def test_uncomposed_or_unbound_reader_does_not_probe_data():
    assert observed_execution_evidence(object(), query())["reason"] == (
        "data72_storage_reader_not_connected"
    )


def test_bounded_verified_data_event_stays_unverified_for_history():
    reader = SimpleNamespace(store=object())

    def read_events(store, **scope):
        assert store is reader.store
        assert scope == dict(category="linear", symbol="BTCUSDT",
                             start_ms=0, end_ms=60000, limit=100)
        return [event()]

    result = observed_execution_evidence(reader, query(), read_events=read_events)
    assert result["status"] == "UNVERIFIED"
    assert result["historical_coverage"] == "UNKNOWN"
    assert result["admitted_to_f"] is False
    assert result["publication_time"] == "UNKNOWN"
    assert result["observed_events"] == 1
    assert result["evidence_revision"].startswith("sample:")
    assert result["first_observed_ms"] == 5000


def test_full_page_or_late_observation_never_claims_complete_history():
    reader = SimpleNamespace(store=object())

    def full_page(*args, **kwargs):
        return [event(exec_id=str(i)) for i in range(100)]

    result = observed_execution_evidence(reader, query(), read_events=full_page)
    assert result["reason"] == "data72_full_page_boundary_unproven"
    assert "evidence_revision" not in result

    late = observed_execution_evidence(
        reader, query(information_cutoff_ms=4000),
        read_events=lambda *a, **kw: [event()],
    )
    assert late["reason"] == "data72_late_source_knowledge"


def test_invalid_units_unknown_publication_and_missing_hashes_fail_closed():
    reader = SimpleNamespace(store=object())
    for invalid in (
        event(quantity_unit="source_native_inverse_contract"),
        event(publication_ms=123),
        event(source_receipts=[]),
        event(source_receipts=[
            dict(evidence_id="ledger-1", page_ref="page:1",
                 http_sha256="not-a-hash", page_sha256="b" * 64,
                 row_sha256="c" * 64)
        ]),
    ):
        result = observed_execution_evidence(
            reader, query(), read_events=lambda *a, **kw: [invalid]
        )
        assert result["status"] == "UNVERIFIED"
        assert "evidence_revision" not in result



def test_wrong_scope_source_hash_or_decimal_cannot_gain_a_revision():
    reader = SimpleNamespace(store=object())
    cases = [
        event(utc_interval=dict(start_ms=1, end_ms=60000)),
        event(price=100.0),
        event(quantity="1e-5"),
        event(provenance=[dict(
            source_kind="bybit_rest",
            source_ref="https://api.bybit.com/v5/market/recent-trade?"
                       "category=linear&symbol=BTCUSDT",
            source_hash="d" * 64, timestamp_precision="ms",
            source_timestamp="1000", ingestion_run="receipt-run",
        )]),
    ]
    for invalid in cases:
        result = observed_execution_evidence(
            reader, query(), read_events=lambda *a, **kw: [invalid]
        )
        assert result["status"] == "UNVERIFIED"
        assert "evidence_revision" not in result


def test_no_data_is_not_proof_of_empty_market():
    reader = SimpleNamespace(store=object())
    result = observed_execution_evidence(
        reader, query(), read_events=lambda *a, **kw: []
    )
    assert result["reason"] == "data72_no_execution_evidence"
    assert result["historical_coverage"] == "UNKNOWN"
    assert "observed_events" not in result
