"""RESEARCH against versioned DATA #72 public execution events, no live orders."""
import base64
import hashlib
import importlib
import json
import os
from datetime import UTC, datetime
from unittest.mock import Mock
from urllib.parse import urlencode

import pytest

from src.integration.data_research_adapter import (
    DATA_CONTRACT_VERSION,
    project_events,
    read_data_metrics,
    require_strategy_lab_f_input,
)

START = 1704067200000
END = START + 60000
OBSERVED_MS = 1794560000000


def event(trade_id="a", *, price="100", quantity="2", side="Buy",
          event_ms=START + 1000):
    return {
        "contract_version": DATA_CONTRACT_VERSION,
        "venue": "bybit", "category": "linear", "symbol": "BTCUSDT",
        "exec_id": trade_id, "taker_side": side,
        "price": price, "quantity": quantity,
        "quantity_unit": "source_native_linear_size",
        "event_ms": event_ms, "observed_ms": OBSERVED_MS,
        "publication_ms": None,
        "utc_interval": {"start_ms": START, "end_ms": END},
        "source_coverage": {"status": "UNKNOWN", "start_ms": START, "end_ms": END},
        "availability": {"status": "UNVERIFIED", "reason": "Public recent executions only"},
        "provenance": [{
            "source_kind": "bybit_rest",
            "source_ref": "https://api.bybit.com/v5/market/recent-trade",
            "source_hash": "c" * 64, "ingestion_run": "source-run",
            "timestamp_precision": "ms", "source_timestamp": str(event_ms),
            "parent_refs": ["evidence-1", "b" * 64, "a" * 64],
            "method_version": DATA_CONTRACT_VERSION,
        }],
        "source_receipts": [{
            "evidence_id": "evidence-1", "page_ref": "f" * 64,
            "http_sha256": "a" * 64, "page_sha256": "b" * 64,
            "row_sha256": "c" * 64,
        }],
    }


def preview(rows):
    return project_events(
        rows, category="linear", symbol="BTCUSDT",
        start_ms=START, end_ms=END, interval_ms=60000, tick_size="0.5"
    )


def test_exact_observed_sample_footprint_delta_cvd_vwap_poc():
    outcome = preview([event(), event("b", price="102", quantity="1", side="Sell")])
    bar = outcome["metrics"]["bars"][0]
    assert bar["volume"] == "3"
    assert bar["delta_known"] == "1"
    assert bar["cvd_known"] == "1"
    assert bar["poc"] == "100"
    assert bar["vwap"].startswith("100.666666666")
    assert len(bar["footprint"]) == 2
    assert outcome["admitted_to_f"] is False
    assert outcome["f_input"] is None
    assert outcome["status"] == "OBSERVED_UNVERIFIED"
    assert outcome["source_verification"] == "CALLER_UNATTESTED"
    assert outcome["observed_window"]["observed_nonempty_bars"] == 1
    assert outcome["observed_window"]["historical_completeness_state"] == "UNKNOWN"


def test_unknown_side_retained_never_bid_ask_inferred():
    outcome = preview([event(side="Unknown")])
    bar = outcome["metrics"]["bars"][0]
    assert bar["unknown_side_volume"] == "2"
    assert bar["delta_known"] == "0"
    assert bar["delta_complete"] is False
    assert bar["footprint"][0]["unknown_side_volume"] == "2"
    assert outcome["publication_time"] == "UNKNOWN"


def test_reordered_events_produce_identical_metrics():
    a, b = event("a", event_ms=START + 2000), event("b", side="Sell")
    assert preview([a, b])["metrics"] == preview([b, a])["metrics"]


@pytest.mark.parametrize("edit,reason", [
    (lambda e: e.__setitem__("contract_version", "other"), "unknown_data_execution_contract"),
    (lambda e: e.__setitem__("quantity_unit", "source_native_inverse_contract"), "market_or_quantity_unit_mismatch"),
    (lambda e: e.__setitem__("symbol", "ETHUSDT"), "market_or_quantity_unit_mismatch"),
    (lambda e: e.__setitem__("publication_ms", START), "publication_time_not_proven"),
    (lambda e: e.pop("publication_ms"), "publication_time_not_proven"),
    (lambda e: e["source_coverage"].__setitem__("status", "COMPLETE"), "historical_scope_or_coverage_mismatch"),
    (lambda e: e["utc_interval"].__setitem__("end_ms", END + 1), "historical_scope_or_coverage_mismatch"),
    (lambda e: e["availability"].__setitem__("status", "AVAILABLE"), "source_availability_not_proven"),
    (lambda e: e.__setitem__("taker_side", "bid"), "invalid_or_inferred_taker_side"),
    (lambda e: e["source_receipts"].clear(), "original_http_page_row_receipts_required"),
    (lambda e: e["source_receipts"][0].__setitem__("page_sha256", "not-a-sha"), "unqualified_source_sha256"),
    (lambda e: e["provenance"][0]["parent_refs"].clear(), "source_receipt_provenance_mismatch"),
    (lambda e: e["source_receipts"][0].__setitem__("row_sha256", "d" * 64), "source_receipt_provenance_mismatch"),
    (lambda e: e["source_receipts"].append(dict(e["source_receipts"][0])), "duplicate_source_receipt"),
    (lambda e: e.__setitem__("event_ms", float(START + 1)), "invalid_event_or_observation_time"),
])
def test_unqualified_event_refused(edit, reason):
    row = event()
    edit(row)
    with pytest.raises(ValueError, match=reason):
        preview([row])


def test_zero_quantity_refused():
    with pytest.raises(ValueError):
        preview([event(quantity="0")])


def test_duplicate_identity_refused_even_if_economics_differ():
    with pytest.raises(ValueError, match="duplicate_exchange_exec_id"):
        preview([event("a"), event("a", price="101")])


def test_late_observation_as_of_fails_closed():
    with pytest.raises(ValueError, match="late_source_knowledge"):
        project_events(
            [event()], category="linear", symbol="BTCUSDT",
            start_ms=START, end_ms=END, interval_ms=60000,
            tick_size="0.5", as_of_ms=START + 60000
        )


def test_inverse_rejected_before_any_data_read():
    store = Mock()
    with pytest.raises(ValueError, match="inverse_quantity_unit_not_comparable"):
        read_data_metrics(
            store, category="inverse", symbol="BTCUSDT",
            start_ms=START, end_ms=END, interval_ms=60000, tick_size="0.5"
        )
    store.read.assert_not_called()


def test_bounded_100_event_window_and_no_data():
    assert preview([])["status"] == "NO_DATA"
    assert preview([])["f_input"] is None
    with pytest.raises(ValueError, match="events_exceed_bounded_read"):
        preview([event(str(i)) for i in range(101)])


def _composed_data():
    try:
        return importlib.import_module("src.ingest.execution_contract")
    except ModuleNotFoundError as exc:
        if exc.name == "src.ingest.execution_contract":
            pytest.skip("DATA #72 has not yet been composed into RESEARCH #71")
        raise


def test_actual_data72_versioned_event_projection_on_composed_branch():
    data = _composed_data()
    if data.VERSION != DATA_CONTRACT_VERSION:
        pytest.fail("DATA #72 version skew")
    from tests.test_execution_contract_bridge import MS, fixture

    window = (MS // 60000) * 60000
    store = Mock()
    store.read.return_value = [fixture(side="Unknown")]
    outcome = read_data_metrics(
        store, category="linear", symbol="BTCUSDT",
        start_ms=window, end_ms=window + 60000,
        interval_ms=60000, tick_size="0.5", limit=10
    )
    assert outcome["source_verification"] == "DATA72_PERSISTED_HTTP_ROW_PAGE_RECEIPTS"
    assert outcome["metrics"]["bars"][0]["unknown_side_volume"] == "0.125"
    assert outcome["metrics"]["bars"][0]["delta_known"] == "0"
    assert outcome["f_input"] is None
    store.read.assert_called_once_with(
        "trades", "bybit", "linear", "BTCUSDT", window, window + 60000, "",
        limit=10, compact_provenance=False
    )
    store.ingest.assert_not_called()


@pytest.mark.integration
def test_disposable_postgres_actual_data72_source_to_research():
    dsn = os.environ.get("TESTER_RESEARCH_DATA_TEST_DSN")
    if not dsn:
        pytest.skip("explicit disposable PostgreSQL DSN absent")
    if "tester_research_data_disposable" not in dsn:
        raise ValueError("refusing non-disposable PostgreSQL database")
    _composed_data()
    import psycopg
    from src.ingest.public_auxiliary import fetch_and_ingest

    from src.storage.migrations import migrate
    from src.storage.store import Store

    with psycopg.connect(dsn) as connection:
        migrate(connection)
    original = {
        "symbol": "BTCUSDT", "execId": "research-data72-pg-v1",
        "time": "1600000000000", "price": "10234.56000",
        "size": "0.125000", "side": "Buy",
    }
    result = {"list": [original]}
    url = "https://api.bybit.com/v5/market/recent-trade?" + urlencode(
        {"category": "linear", "symbol": "BTCUSDT", "limit": 1000}
    )
    original_http = json.dumps({"retCode": 0, "result": result}).encode()
    sha = hashlib.sha256(original_http).hexdigest()

    class SourceReplay:
        def __init__(self):
            self.last_response = {
                "url": url, "status_code": 200, "hash_scope": "original_http_body",
                "sha256": sha,
                "bytes_base64": base64.b64encode(original_http).decode(),
            }

        def call(self, path, params):
            assert path == "/recent-trade"
            assert params == {"category": "linear", "symbol": "BTCUSDT", "limit": 1000}
            return result, url

    store = Store(dsn)
    for _ in range(2):
        fetch_and_ingest(
            store, SourceReplay(), kind="trades", category="linear",
            symbol="BTCUSDT", run_id="research-data72-pg-run-v1",
            job_key="research-data72-pg-job-v1",
            observed_at=datetime(2026, 11, 13, 20, tzinfo=UTC)
        )
    reopened = Store(dsn)
    stamp = 1600000000000
    start = stamp // 60000 * 60000
    outcome = read_data_metrics(
        reopened, category="linear", symbol="BTCUSDT",
        start_ms=start, end_ms=start + 60000, interval_ms=60000,
        tick_size="0.5", limit=10
    )
    assert outcome["metrics"]["bars"][0]["vwap"] == "10234.56"
    assert outcome["metrics"]["bars"][0]["delta_known"] == "0.125"
    assert outcome["observed_window"]["observed_unique_trades"] == 1
    assert outcome["coverage"] == "UNKNOWN"
    assert outcome["admitted_to_f"] is False
    assert outcome["f_input"] is None
    assert outcome["persisted_dataset_revision"] is None
    assert outcome["historical_known_at_ms"] is None
    assert len(outcome["observed_sample_fingerprint"]) == 64
    with pytest.raises(ValueError, match="real_historical_visibility_unknown_f_denied"):
        require_strategy_lab_f_input(outcome)


def test_missing_data72_module_fails_closed(monkeypatch):
    import src.integration.data_research_adapter as adapter

    def missing(name):
        raise ModuleNotFoundError("not composed", name=name)

    monkeypatch.setattr(adapter.importlib, "import_module", missing)
    store = Mock()
    with pytest.raises(ValueError, match="data72_versioned_contract_not_composed"):
        read_data_metrics(
            store, category="linear", symbol="BTCUSDT",
            start_ms=START, end_ms=END, interval_ms=60000, tick_size="0.5"
        )
    store.read.assert_not_called()


def test_data72_version_skew_refused_before_read(monkeypatch):
    from types import SimpleNamespace

    import src.integration.data_research_adapter as adapter

    monkeypatch.setattr(adapter.importlib, "import_module",
                        lambda name: SimpleNamespace(VERSION="tester-public-execution/2"))
    store = Mock()
    with pytest.raises(ValueError, match="data_execution_contract_version_skew"):
        read_data_metrics(
            store, category="linear", symbol="BTCUSDT",
            start_ms=START, end_ms=END, interval_ms=60000, tick_size="0.5"
        )
    store.read.assert_not_called()


@pytest.mark.parametrize("interval", [
    "1", "3", "5", "15", "30", "60", "120", "240", "360",
    "720", "D", "W", "M",
])
def test_all_thirteen_utc_exchange_interval_boundaries(interval):
    """Timeframe boundary contract only; NOT chart/browser acceptance."""
    from src.analytics.core import interval_bounds

    start, end = interval_bounds(1794560000000, interval)
    assert start <= 1794560000000 < end
    if interval == "W":
        assert datetime.fromtimestamp(start // 1000, UTC).weekday() == 0
    if interval == "M":
        assert datetime.fromtimestamp(start // 1000, UTC).day == 1
    if interval.isdigit():
        assert end - start == int(interval) * 60000


def test_full_source_page_never_claims_complete_sample(monkeypatch):
    from types import SimpleNamespace

    import src.integration.data_research_adapter as adapter

    monkeypatch.setattr(adapter.importlib, "import_module", lambda name: SimpleNamespace(
        VERSION=DATA_CONTRACT_VERSION,
        read_execution_events=lambda *args, **kwargs: [event()],
        to_research_trade=lambda e: None,
    ))
    with pytest.raises(ValueError, match="data_event_page_limit_requires_pagination"):
        read_data_metrics(
            Mock(), category="linear", symbol="BTCUSDT", start_ms=START,
            end_ms=END, interval_ms=60000, tick_size="0.5", limit=1
        )


def test_empty_data_read_does_not_claim_proof(monkeypatch):
    from types import SimpleNamespace

    import src.integration.data_research_adapter as adapter

    monkeypatch.setattr(adapter.importlib, "import_module", lambda name: SimpleNamespace(
        VERSION=DATA_CONTRACT_VERSION,
        read_execution_events=lambda *args, **kwargs: [],
        to_research_trade=lambda e: None,
    ))
    result = read_data_metrics(
        Mock(), category="linear", symbol="BTCUSDT", start_ms=START,
        end_ms=END, interval_ms=60000, tick_size="0.5", limit=10
    )
    assert result["status"] == "NO_DATA"
    assert result["source_verification"] == "NO_EXECUTION_EVIDENCE"
    assert result["f_input"] is None


def test_strategy_lab_f_denies_unverified_real_samples():
    preview = project_events(
        [event()], category="linear", symbol="BTCUSDT",
        start_ms=START, end_ms=END, interval_ms=60000, tick_size="0.5"
    )
    with pytest.raises(ValueError, match="real_historical_visibility_unknown_f_denied"):
        require_strategy_lab_f_input(preview)
    assert preview["f_input"] is None
    assert preview["g_e_result"] is None


def test_strategy_lab_f_rejects_forged_complete_coverage():
    preview = project_events(
        [event()], category="linear", symbol="BTCUSDT",
        start_ms=START, end_ms=END, interval_ms=60000, tick_size="0.5"
    )
    preview["coverage"] = "COMPLETE"
    with pytest.raises(ValueError, match="research_preview_claims_unreviewed_f_admission"):
        require_strategy_lab_f_input(preview)
    preview["coverage"] = "UNKNOWN"
    preview["observed_window"]["complete_market_history"] = True
    with pytest.raises(ValueError, match="research_history_completeness_not_demonstrated"):
        require_strategy_lab_f_input(preview)


@pytest.mark.integration
def test_disposable_pg_duplicate_replay_unknown_side_capped_and_http_tamper():
    """Actual PG16: duplicate replay is idempotent; HTTP corruption fails closed."""
    dsn = os.environ.get("TESTER_RESEARCH_DATA_TEST_DSN")
    if not dsn:
        pytest.skip("dedicated disposable PG16 not configured")
    if "tester_research_data_disposable" not in dsn:
        raise ValueError("protected: dedicated disposable database required")
    _composed_data()
    import psycopg
    from src.ingest.public_auxiliary import fetch_and_ingest

    from src.storage.migrations import migrate
    from src.storage.store import Store

    with psycopg.connect(dsn) as connection:
        migrate(connection)
    ms = 1600000060000
    original = [
        {"symbol": "BTCUSDT", "execId": "research-pg-side-buy-v2",
         "time": str(ms), "price": "100", "size": "0.5", "side": "Buy"},
        {"symbol": "BTCUSDT", "execId": "research-pg-side-unknown-v2",
         "time": str(ms + 1), "price": "101", "size": "0.25", "side": "Unknown"},
    ]
    result = {"list": original}
    params = {"category": "linear", "symbol": "BTCUSDT", "limit": 1000}
    url = "https://api.bybit.com/v5/market/recent-trade?" + urlencode(params)
    body = json.dumps({"retCode": 0, "result": result}).encode()
    sha = hashlib.sha256(body).hexdigest()

    class HttpProof:
        def __init__(self, *, tampered=False):
            self.last_response = {
                "url": url, "status_code": 200, "hash_scope": "original_http_body",
                "sha256": "0" * 64 if tampered else sha,
                "bytes_base64": base64.b64encode(body).decode(),
            }

        def call(self, path, got):
            assert path == "/recent-trade" and got == params
            return result, url

    kwargs = dict(
        kind="trades", category="linear", symbol="BTCUSDT",
        run_id="research-data72-pg-run-v2",
        job_key="research-data72-pg-job-v2",
        observed_at=datetime(2026, 11, 13, 20, tzinfo=UTC),
    )
    source = Store(dsn)
    for _ in range(2):
        fetch_and_ingest(source, HttpProof(), **kwargs)
    with pytest.raises(ValueError):
        fetch_and_ingest(source, HttpProof(tampered=True), **kwargs)
    reopened = Store(dsn)
    start = ms // 60000 * 60000
    with pytest.raises(ValueError, match="data_event_page_limit_requires_pagination"):
        read_data_metrics(
            reopened, category="linear", symbol="BTCUSDT",
            start_ms=start, end_ms=start + 60000,
            interval_ms=60000, tick_size="0.5", limit=2,
        )
    actual = read_data_metrics(
        reopened, category="linear", symbol="BTCUSDT",
        start_ms=start, end_ms=start + 60000,
        interval_ms=60000, tick_size="0.5", limit=3,
    )
    bar = actual["metrics"]["bars"][0]
    assert actual["observed_window"]["observed_unique_trades"] == 2
    assert bar["unknown_side_volume"] == "0.25"
    assert bar["buy_volume"] == "0.5"
    assert bar["delta_known"] == "0.5"
    assert bar["cvd_known"] == "0.5"
    assert bar["vwap"] == "100.33333333333333333333333333333333333333333333333"
    assert bar["poc"] == "100"
    assert bar["coverage_certified"] is False
    assert actual["admitted_to_f"] is False
    assert actual["f_input"] is None
    assert actual["g_e_result"] is None
    assert actual["historical_known_at_ms"] is None
    assert actual["persisted_dataset_revision"] is None
    with pytest.raises(ValueError, match="real_historical_visibility_unknown_f_denied"):
        require_strategy_lab_f_input(actual)
    assert len(reopened.read(
        "trades", "bybit", "linear", "BTCUSDT",
        start, start + 60000, "", limit=10,
    )) == 2


def test_strategy_lab_f_rejects_malformed_window_without_attribute_error():
    preview = project_events(
        [event()], category="linear", symbol="BTCUSDT",
        start_ms=START, end_ms=END, interval_ms=60000, tick_size="0.5"
    )
    preview["observed_window"] = None
    with pytest.raises(ValueError, match="research_history_completeness_not_demonstrated"):
        require_strategy_lab_f_input(preview)


@pytest.mark.integration
def test_disposable_pg_gapped_observation_window_stays_unknown():
    """A missing minute in original executions is NOT a verified empty market."""
    dsn = os.environ.get("TESTER_RESEARCH_DATA_TEST_DSN")
    if not dsn:
        pytest.skip("disposable PG16 DSN unavailable")
    if "tester_research_data_disposable" not in dsn:
        raise ValueError("protected: disposable PG DB only")
    _composed_data()
    import psycopg
    from src.ingest.public_auxiliary import fetch_and_ingest

    from src.storage.migrations import migrate
    from src.storage.store import Store

    with psycopg.connect(dsn) as connection:
        migrate(connection)
    start = 1600000200000 // 60000 * 60000
    rows = [
        {"symbol": "BTCUSDT", "execId": "research-gap-first-v3",
         "time": str(start + 1000), "price": "100", "size": "1", "side": "Buy"},
        {"symbol": "BTCUSDT", "execId": "research-gap-third-v3",
         "time": str(start + 121000), "price": "102", "size": "1", "side": "Sell"},
    ]
    result = {"list": rows}
    params = {"category": "linear", "symbol": "BTCUSDT", "limit": 1000}
    url = "https://api.bybit.com/v5/market/recent-trade?" + urlencode(params)
    raw = json.dumps({"retCode": 0, "result": result}).encode()

    class Replay:
        last_response = {
            "url": url, "status_code": 200, "hash_scope": "original_http_body",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes_base64": base64.b64encode(raw).decode(),
        }

        def call(self, path, got):
            assert path == "/recent-trade" and got == params
            return result, url

    store = Store(dsn)
    fetch_and_ingest(
        store, Replay(), kind="trades", category="linear", symbol="BTCUSDT",
        run_id="research-gap-v3-run", job_key="research-gap-v3-job",
        observed_at=datetime(2026, 11, 13, 20, tzinfo=UTC)
    )
    reopened = Store(dsn)
    preview = read_data_metrics(
        reopened, category="linear", symbol="BTCUSDT",
        start_ms=start, end_ms=start + 180000,
        interval_ms=60000, tick_size="0.5", limit=3
    )
    assert preview["status"] == "OBSERVED_UNVERIFIED"
    bars = preview["metrics"]["bars"]
    assert [b["trade_count"] for b in bars] == [1, 0, 1]
    assert [b["cvd_known"] for b in bars] == ["1", "1", "0"]
    assert bars[1]["vwap"] is None
    assert bars[1]["poc"] is None
    assert "empty_interval" in bars[1]["reasons"]
    assert preview["observed_window"]["empty_bars_in_observed_sample"] == 1
    assert preview["observed_window"]["complete_market_history"] is False
    with pytest.raises(ValueError, match="real_historical_visibility_unknown_f_denied"):
        require_strategy_lab_f_input(preview)



@pytest.mark.parametrize("dataset", ["open_interest", "funding", "candles", "mark", "index"])
def test_non_execution_dataset_cannot_be_projected(dataset):
    """Only verified public trade events are eligible for these statistics."""
    wrong = event()
    wrong["contract_version"] = f"tester-{dataset}/1"
    with pytest.raises(ValueError, match="unknown_data_execution_contract"):
        preview([wrong])
