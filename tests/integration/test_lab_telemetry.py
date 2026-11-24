import hashlib
from types import SimpleNamespace

import pytest


def test_http_meter_measures_body_bytes_and_monotonic_time_without_dataset_claim():
    from src.integration.lab_telemetry import HttpMeter

    ticks = iter([10.0, 12.0])
    response = SimpleNamespace(content=b"public-body")
    session = SimpleNamespace(request=lambda *a, **k: response, close=lambda: None)
    meter = HttpMeter(session, clock=lambda: next(ticks))
    assert meter.request("GET", "https://api.bybit.com/v5/market/time") is response
    snapshot = meter.snapshot()
    assert snapshot["bytes_received"] == 11
    assert snapshot["elapsed_seconds"] == 2
    assert snapshot["bytes_per_second"] == 5.5
    assert snapshot["evidence_refs"] == [hashlib.sha256(b"public-body").hexdigest()]
    assert snapshot["expected_bytes"] is None


def test_committed_ledger_resumes_without_duplicate_counts_or_history_promotion():
    from src.integration.lab_telemetry import acknowledgment

    measured = dict(
        bytes_received=100, elapsed_seconds=2, bytes_per_second=50, evidence_refs=["a" * 64]
    )
    first = acknowledgment({}, {"key1": "b" * 64}, measured, complete=False)
    assert first["records_ingested"] == first["records_verified"] == 1
    assert first["coverage"] == "UNKNOWN"
    replay = acknowledgment(first, {"key1": "b" * 64, "key2": "c" * 64}, measured, complete=True)
    assert replay["records_ingested"] == replay["records_verified"] == 2
    assert replay["work_complete"] is True
    assert replay["expected_records"] is None
    assert first["records_verified"] == 1
    with pytest.raises(ValueError, match="identity"):
        acknowledgment(first, {"key1": "f" * 64}, measured, complete=True)


def test_prior_attempt_transfer_is_accumulated_once_with_null_for_unmeasured_history():
    from src.integration.lab_telemetry import attempt_measurement

    prior = dict(bytes_received=200, elapsed_seconds=4, evidence_refs=["b" * 64])
    now = dict(
        bytes_received=100,
        elapsed_seconds=2,
        bytes_per_second=50,
        evidence_refs=["a" * 64],
        expected_bytes=None,
    )
    result = attempt_measurement(prior, now)
    assert result["bytes_received"] == 300 and result["elapsed_seconds"] == 6
    assert result["bytes_per_second"] == 50
    assert result["evidence_refs"] == ["b" * 64, "a" * 64]
    assert attempt_measurement({}, now)["bytes_received"] == 100


def test_batch_verification_requires_reread_and_canonical_projection(monkeypatch):
    from src.integration import lab_market
    from src.storage.models import normalize

    raw = dict(
        schema_version="1.0",
        venue="bybit",
        category="linear",
        symbol="BTCUSDT",
        dataset="candles",
        interval="1",
        timestamp_ms=60000,
        payload=dict(open="1", high="2", low="1", close="2", volume="3"),
        source=dict(
            url="https://api.bybit.com/v5/market/kline",
            fetched_at="2026-11-08T00:00:00+00:00",
            sha256="a" * 64,
            ingestion_run="run:original",
        ),
    )
    row = normalize(raw)
    key = row["record_key"]
    evidence = dict(
        venue="bybit",
        category="linear",
        symbol="BTCUSDT",
        interval="1",
        start_ms=60000,
        end_ms=120000,
    )
    store = SimpleNamespace(read=lambda *a, **k: [row])
    connection = SimpleNamespace(
        execute=lambda *a, **k: SimpleNamespace(fetchall=lambda: [(key, {"fixture": "projection"})])
    )
    calls = []
    monkeypatch.setattr(lab_market, "project_candle", lambda *args: calls.append(args))
    assert lab_market.verify_published_batch(
        store, connection, dict(records=[raw], evidence=evidence)
    ) == {key: row["content_hash"]}
    assert calls[0][1] == {"fixture": "projection"}
    store.read = lambda *a, **k: []
    with pytest.raises(ValueError, match="missing"):
        lab_market.verify_published_batch(store, connection, dict(records=[raw], evidence=evidence))
    store.read = lambda *a, **k: [dict(row, content_hash="f" * 64)]
    with pytest.raises(ValueError, match="differ"):
        lab_market.verify_published_batch(store, connection, dict(records=[raw], evidence=evidence))


def test_calendar_downloads_are_bounded_but_can_include_closed_months_and_weeks():
    from src.api.providers import ProviderUnavailable
    from src.integration.lab_market import validate_download

    for interval, days in [("W", 7), ("M", 31)]:
        request = SimpleNamespace(
            category="linear",
            symbol="BTCUSDT",
            source="bybit",
            kind="candles",
            interval=interval,
            start_ms=0,
            end_ms=days * 86400000,
        )
        validate_download(request)
        request.end_ms = 94 * 86400000
        with pytest.raises(ProviderUnavailable):
            validate_download(request)


def test_measured_bytes_are_bound_to_retained_original_http_bodies(tmp_path):
    from src.integration.lab_telemetry import HttpMeter

    response = SimpleNamespace(content=b"actual-wire-body")
    session = SimpleNamespace(request=lambda *a, **k: response, close=lambda: None)
    meter = HttpMeter(session, evidence_dir=tmp_path)
    meter.request("GET", "https://api.bybit.com/v5/market/time")
    receipt = meter.snapshot()["response_receipts"][0]
    assert receipt["bytes"] == len(response.content)
    assert receipt["source_ref"] == "https://api.bybit.com/v5/market/time"
    assert (tmp_path / (receipt["source_hash"] + ".http-body")).read_bytes() == response.content
