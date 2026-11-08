from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.api.providers import ProviderUnavailable


def test_public_source_backoff_is_bounded_and_cancelable():
    import threading
    import time

    from src.integration.lab_market import source_wait

    stop = threading.Event()
    with pytest.raises(ProviderUnavailable):
        source_wait(stop, time.monotonic() + 30, 600)
    stop.set()
    with pytest.raises(InterruptedError):
        source_wait(stop, time.monotonic() + 30, 0)


def test_conflict_is_republished_under_a_second_fence_then_raised():
    from src.integration.lab_market import commit_batch
    from src.storage.store import DataConflict

    events = []

    def fence(write, checkpoint, progress):
        events.append("fence")
        write(None)

    def write(connection):
        events.append("conflict")
        raise DataConflict("fixture conflict")

    def mark(connection):
        events.append("mark_corrupt")

    with pytest.raises(DataConflict):
        commit_batch(fence, write, mark, {}, 0.5)
    assert events == ["fence", "conflict", "fence", "mark_corrupt"]


def test_readiness_valid_degraded_response_has_bounded_deadline():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts/lab_portable.py"
    spec = importlib.util.spec_from_file_location("lab_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ticks = iter([0, 1, 2, 3])
    with pytest.raises(RuntimeError, match="readiness"):
        module.wait_ready(
            lambda: {"data": {"status": "degraded"}},
            timeout=2,
            clock=lambda: next(ticks),
            sleep=lambda _: None,
        )


def test_lab_handler_rejects_unbounded_and_non_public_work_before_network():
    from src.integration.lab_market import validate_download

    base = dict(
        category="linear",
        symbol="BTCUSDT",
        source="bybit",
        kind="candles",
        interval="1",
        start_ms=60000,
        end_ms=120000,
    )
    validate_download(SimpleNamespace(**base))
    for changes in [
        {"kind": "trades"},
        {"source": "private"},
        {"symbol": "OTHER"},
        {"end_ms": 90000000},
        {"category": "spot"},
    ]:
        with pytest.raises(ProviderUnavailable):
            validate_download(SimpleNamespace(**(base | changes)))


def test_candle_projection_requires_b_identity_and_original_provenance():
    from src.integration.lab_market import project_candle
    from src.storage.models import normalize

    source = dict(
        url="https://api.bybit.com/v5/market/kline",
        fetched_at="2026-11-08T00:00:00+00:00",
        sha256="a" * 64,
        ingestion_run="run:original",
    )
    raw = dict(
        schema_version="1.0",
        venue="bybit",
        category="linear",
        symbol="BTCUSDT",
        dataset="candles",
        interval="1",
        timestamp_ms=60000,
        payload=dict(open="1", high="2", low="1", close="2", volume="3"),
        source=source,
    )
    row = normalize(raw)
    row["provenance"] = dict(
        authentic=True,
        unlinked_sources=[],
        observations=[
            dict(
                authentic_run=True,
                evidence_id="e1",
                source=source,
                run_id="run:original",
                source_kind="rest",
                timestamp_precision="ms",
                source_run_status="matched",
                source_ingestion_run="run:original",
            )
        ],
    )
    canonical = dict(
        schema_version="1.0",
        venue="bybit",
        category="linear",
        symbol="BTCUSDT",
        timestamp_ms=60000,
        start_ms=60000,
        end_ms=120000,
        interval="1",
        kind="official_trade",
        is_closed=True,
        open="1",
        high="2",
        low="1",
        close="2",
        volume="3",
        provenance=[
            dict(
                source_kind="bybit_rest",
                source_ref=source["url"],
                source_hash="a" * 64,
                ingestion_run="run:original",
                timestamp_precision="ms",
                source_timestamp=None,
            )
        ],
        availability=dict(status="PARTIAL", reason="bounded observation"),
        coverage=dict(status="PARTIAL", start_ms=60000, end_ms=120000, evidence_refs=["e1"]),
    )
    result = project_candle(row, canonical, 60000, 120000)
    assert result["coverage"]["status"] == "UNKNOWN"
    assert result["availability"]["status"] == "PARTIAL"
    for mutation in ["close", "run", "authentic"]:
        changed = deepcopy(canonical)
        b = deepcopy(row)
        if mutation == "close":
            changed["close"] = "1.5"
        if mutation == "run":
            changed["provenance"][0]["ingestion_run"] = "run:invented"
        if mutation == "authentic":
            b["provenance"]["authentic"] = False
        with pytest.raises(ProviderUnavailable):
            project_candle(b, changed, 60000, 120000)
