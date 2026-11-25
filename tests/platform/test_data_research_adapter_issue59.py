"""RESEARCH against versioned DATA #72 public execution events, no live orders."""

import base64
import hashlib
import importlib
import json
import os
from collections.abc import Mapping
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
from src.integration.research_ui_projection import (
    project_source_observation,
    read_verified_ui_observation,
)
from src.storage.models import digest

START = 1704067200000
END = START + 60000
OBSERVED_MS = 1794560000000


def _constraint_definitions(rows):
    """Extract pg_constraint names from psycopg dict_row or tuple_row safely."""
    definitions = {}
    for row in rows:
        if isinstance(row, Mapping):
            name, clause = row["conname"], row["definition"]
        else:
            name, clause = row
        if (
            not isinstance(name, str)
            or not isinstance(clause, str)
            or not name
            or not clause
            or name in definitions
        ):
            raise ValueError("invalid_or_duplicate_pg_constraint_row")
        definitions[name] = clause
    return definitions


@pytest.mark.parametrize(
    "rows",
    [
        [
            {
                "conname": "raw_records_check2",
                "definition": "CHECK (price > 0 AND side IN ('Buy', 'Sell', 'Unknown'))",
            },
            {
                "conname": "trade_side_required",
                "definition": "CHECK (side IN ('Buy', 'Sell', 'Unknown'))",
            },
        ],
        [
            ("raw_records_check2", "CHECK (price > 0 AND side IN ('Buy', 'Sell', 'Unknown'))"),
            ("trade_side_required", "CHECK (side IN ('Buy', 'Sell', 'Unknown'))"),
        ],
    ],
    ids=("dict_row", "tuple_row"),
)
def test_pg_constraint_result_rows_keep_names_and_definitions(rows):
    constraints = _constraint_definitions(rows)
    assert set(constraints) == {"raw_records_check2", "trade_side_required"}
    assert all("'Unknown'" in clause for clause in constraints.values())
    assert "price > 0" in constraints["raw_records_check2"]


@pytest.mark.parametrize(
    "rows",
    [
        [
            {"conname": "raw_records_check2", "definition": "CHECK (x > 0)"},
            {"conname": "raw_records_check2", "definition": "CHECK (x > 0)"},
        ],
        [("raw_records_check2", "CHECK (x > 0)"), ("raw_records_check2", "CHECK (x > 0)")],
    ],
    ids=("duplicate_dict_row", "duplicate_tuple_row"),
)
def test_pg_constraint_duplicate_rows_fail_closed(rows):
    with pytest.raises(ValueError, match="invalid_or_duplicate_pg_constraint_row"):
        _constraint_definitions(rows)


def event(trade_id="a", *, price="100", quantity="2", side="Buy", event_ms=START + 1000):
    return {
        "contract_version": DATA_CONTRACT_VERSION,
        "venue": "bybit",
        "category": "linear",
        "symbol": "BTCUSDT",
        "exec_id": trade_id,
        "taker_side": side,
        "price": price,
        "quantity": quantity,
        "quantity_unit": "source_native_linear_size",
        "event_ms": event_ms,
        "observed_ms": OBSERVED_MS,
        "publication_ms": None,
        "utc_interval": {"start_ms": START, "end_ms": END},
        "source_coverage": {"status": "UNKNOWN", "start_ms": START, "end_ms": END},
        "availability": {"status": "UNVERIFIED", "reason": "Public recent executions only"},
        "provenance": [
            {
                "source_kind": "bybit_rest",
                "source_ref": "https://api.bybit.com/v5/market/recent-trade",
                "source_hash": "c" * 64,
                "ingestion_run": "source-run",
                "timestamp_precision": "ms",
                "source_timestamp": str(event_ms),
                "parent_refs": ["evidence-1", "b" * 64, "a" * 64],
                "method_version": DATA_CONTRACT_VERSION,
            }
        ],
        "source_receipts": [
            {
                "evidence_id": "evidence-1",
                "page_ref": "f" * 64,
                "http_sha256": "a" * 64,
                "page_sha256": "b" * 64,
                "row_sha256": "c" * 64,
            }
        ],
    }


def preview(rows):
    return project_events(
        rows,
        category="linear",
        symbol="BTCUSDT",
        start_ms=START,
        end_ms=END,
        interval_ms=60000,
        tick_size="0.5",
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


@pytest.mark.parametrize(
    "edit,reason",
    [
        (lambda e: e.__setitem__("contract_version", "other"), "unknown_data_execution_contract"),
        (
            lambda e: e.__setitem__("quantity_unit", "source_native_inverse_contract"),
            "market_or_quantity_unit_mismatch",
        ),
        (lambda e: e.__setitem__("symbol", "ETHUSDT"), "market_or_quantity_unit_mismatch"),
        (lambda e: e.__setitem__("publication_ms", START), "publication_time_not_proven"),
        (lambda e: e.pop("publication_ms"), "publication_time_not_proven"),
        (
            lambda e: e["source_coverage"].__setitem__("status", "COMPLETE"),
            "historical_scope_or_coverage_mismatch",
        ),
        (
            lambda e: e["utc_interval"].__setitem__("end_ms", END + 1),
            "historical_scope_or_coverage_mismatch",
        ),
        (
            lambda e: e["availability"].__setitem__("status", "AVAILABLE"),
            "source_availability_not_proven",
        ),
        (lambda e: e.__setitem__("taker_side", "bid"), "invalid_or_inferred_taker_side"),
        (lambda e: e["source_receipts"].clear(), "original_http_page_row_receipts_required"),
        (
            lambda e: e["source_receipts"][0].__setitem__("page_sha256", "not-a-sha"),
            "unqualified_source_sha256",
        ),
        (lambda e: e["provenance"][0]["parent_refs"].clear(), "source_receipt_provenance_mismatch"),
        (
            lambda e: e["source_receipts"][0].__setitem__("row_sha256", "d" * 64),
            "source_receipt_provenance_mismatch",
        ),
        (
            lambda e: e["source_receipts"].append(dict(e["source_receipts"][0])),
            "duplicate_source_receipt",
        ),
        (
            lambda e: e.__setitem__("event_ms", float(START + 1)),
            "invalid_event_or_observation_time",
        ),
    ],
)
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
            [event()],
            category="linear",
            symbol="BTCUSDT",
            start_ms=START,
            end_ms=END,
            interval_ms=60000,
            tick_size="0.5",
            as_of_ms=START + 60000,
        )


def test_real_data_declares_unit_matched_inverse_projection():
    # Before: DATA declared no RESEARCH_CATEGORIES, so read_data_metrics refused
    # inverse with inverse_quantity_unit_not_comparable before any read.
    import src.ingest.execution_contract as data
    from src.integration.data_research_adapter import SOURCE_QUANTITY_UNITS

    assert data.RESEARCH_CATEGORIES == ("linear", "inverse")
    assert data.SOURCE_QUANTITY_UNITS == SOURCE_QUANTITY_UNITS
    assert "spot" not in data.RESEARCH_CATEGORIES


def test_spot_rejected_before_any_data_read():
    store = Mock()
    with pytest.raises(ValueError, match="execution_category_not_comparable"):
        read_data_metrics(
            store,
            category="spot",
            symbol="BTCUSDT",
            start_ms=START,
            end_ms=END,
            interval_ms=60000,
            tick_size="0.5",
        )
    store.read_bounded.assert_not_called()


def test_inverse_refused_before_read_when_data_unit_table_skews(monkeypatch):
    from types import SimpleNamespace

    import src.integration.data_research_adapter as adapter

    for units in (None, {"inverse": "source_native_linear_size"}):
        fake = SimpleNamespace(
            VERSION=DATA_CONTRACT_VERSION, RESEARCH_CATEGORIES=("linear", "inverse")
        )
        if units is not None:
            fake.SOURCE_QUANTITY_UNITS = units
        monkeypatch.setattr(adapter.importlib, "import_module", lambda name, f=fake: f)
        store = Mock()
        with pytest.raises(ValueError, match="data_source_quantity_unit_skew"):
            read_data_metrics(
                store,
                category="inverse",
                symbol="BTCUSD",
                start_ms=START,
                end_ms=END,
                interval_ms=60000,
                tick_size="1",
            )
        store.read_bounded.assert_not_called()


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
    from tests.test_execution_contract_bridge import MS, compact_store, fixture

    window = (MS // 60000) * 60000
    store = compact_store([fixture(side="Unknown")])
    outcome = read_data_metrics(
        store,
        category="linear",
        symbol="BTCUSDT",
        start_ms=window,
        end_ms=window + 60000,
        interval_ms=60000,
        tick_size="0.5",
        limit=10,
    )
    assert outcome["source_verification"] == "DATA72_PERSISTED_HTTP_ROW_PAGE_RECEIPTS"
    assert outcome["metrics"]["bars"][0]["unknown_side_volume"] == "0.125"
    assert outcome["metrics"]["bars"][0]["delta_known"] == "0"
    assert outcome["f_input"] is None
    ui = project_source_observation(outcome)
    assert ui["bars"][0]["unknown_side_volume"] == "0.125"
    assert ui["source_coverage"] == "UNKNOWN"
    assert ui["f_admission"] == "DENIED_UNKNOWN_COVERAGE"
    assert ui["g_e_execution"] == "NOT_AUTHORIZED"
    assert ui["http_route"] is None
    store.read_bounded.assert_called_once_with(
        "trades",
        "bybit",
        "linear",
        "BTCUSDT",
        window,
        window + 60000,
        "",
        limit=10,
        budget=data.READ_BUDGET,
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

    from src.storage.migrations import migrate
    from src.storage.store import Store

    # DATA #72 is opt-in compositional source: resolve it after the pin gate,
    # without changing Ruff import groups based on its physical presence.
    fetch_and_ingest = importlib.import_module("src.ingest.public_auxiliary").fetch_and_ingest

    with psycopg.connect(dsn) as connection:
        migrate(connection)
    original = {
        "symbol": "BTCUSDT",
        "execId": "research-data72-pg-v1",
        "time": "1600000000000",
        "price": "10234.56000",
        "size": "0.125000",
        "side": "Buy",
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
                "url": url,
                "status_code": 200,
                "hash_scope": "original_http_body",
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
            store,
            SourceReplay(),
            kind="trades",
            category="linear",
            symbol="BTCUSDT",
            run_id="research-data72-pg-run-v1",
            job_key="research-data72-pg-job-v1",
            observed_at=datetime(2026, 11, 13, 20, tzinfo=UTC),
        )
    reopened = Store(dsn)
    stamp = 1600000000000
    start = stamp // 60000 * 60000
    outcome = read_data_metrics(
        reopened,
        category="linear",
        symbol="BTCUSDT",
        start_ms=start,
        end_ms=start + 60000,
        interval_ms=60000,
        tick_size="0.5",
        limit=10,
    )
    assert outcome["metrics"]["bars"][0]["vwap"] == "10234.56"
    assert outcome["metrics"]["bars"][0]["delta_known"] == "0.125"
    # Real PostgreSQL DATA readback now writes execution-metrics/2.
    assert outcome["metrics"]["method"] == "tester.execution-metrics/2"
    assert outcome["metrics"]["quantity_unit"] == "base"
    assert outcome["metrics_digest"] == digest(outcome["metrics"])
    assert project_source_observation(outcome)["metrics_contract"] == {
        "method": "tester.execution-metrics/2",
        "quantity_unit": "base",
        "status": "CURRENT",
    }
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
            store,
            category="linear",
            symbol="BTCUSDT",
            start_ms=START,
            end_ms=END,
            interval_ms=60000,
            tick_size="0.5",
        )
    store.read_bounded.assert_not_called()


def test_data72_version_skew_refused_before_read(monkeypatch):
    from types import SimpleNamespace

    import src.integration.data_research_adapter as adapter

    monkeypatch.setattr(
        adapter.importlib,
        "import_module",
        lambda name: SimpleNamespace(VERSION="tester-public-execution/2"),
    )
    store = Mock()
    with pytest.raises(ValueError, match="data_execution_contract_version_skew"):
        read_data_metrics(
            store,
            category="linear",
            symbol="BTCUSDT",
            start_ms=START,
            end_ms=END,
            interval_ms=60000,
            tick_size="0.5",
        )
    store.read_bounded.assert_not_called()


@pytest.mark.parametrize(
    "interval",
    [
        "1",
        "3",
        "5",
        "15",
        "30",
        "60",
        "120",
        "240",
        "360",
        "720",
        "D",
        "W",
        "M",
    ],
)
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

    monkeypatch.setattr(
        adapter.importlib,
        "import_module",
        lambda name: SimpleNamespace(
            VERSION=DATA_CONTRACT_VERSION,
            read_execution_events=lambda *args, **kwargs: [event()],
            to_research_trade=lambda e: None,
        ),
    )
    with pytest.raises(ValueError, match="data_event_page_limit_requires_pagination"):
        read_data_metrics(
            Mock(),
            category="linear",
            symbol="BTCUSDT",
            start_ms=START,
            end_ms=END,
            interval_ms=60000,
            tick_size="0.5",
            limit=1,
        )


def test_empty_data_read_does_not_claim_proof(monkeypatch):
    from types import SimpleNamespace

    import src.integration.data_research_adapter as adapter

    monkeypatch.setattr(
        adapter.importlib,
        "import_module",
        lambda name: SimpleNamespace(
            VERSION=DATA_CONTRACT_VERSION,
            read_execution_events=lambda *args, **kwargs: [],
            to_research_trade=lambda e: None,
        ),
    )
    result = read_data_metrics(
        Mock(),
        category="linear",
        symbol="BTCUSDT",
        start_ms=START,
        end_ms=END,
        interval_ms=60000,
        tick_size="0.5",
        limit=10,
    )
    assert result["status"] == "NO_DATA"
    assert result["source_verification"] == "NO_EXECUTION_EVIDENCE"
    assert result["f_input"] is None


def test_strategy_lab_f_denies_unverified_real_samples():
    preview = project_events(
        [event()],
        category="linear",
        symbol="BTCUSDT",
        start_ms=START,
        end_ms=END,
        interval_ms=60000,
        tick_size="0.5",
    )
    with pytest.raises(ValueError, match="real_historical_visibility_unknown_f_denied"):
        require_strategy_lab_f_input(preview)
    assert preview["f_input"] is None
    assert preview["g_e_result"] is None


def test_strategy_lab_f_rejects_forged_complete_coverage():
    preview = project_events(
        [event()],
        category="linear",
        symbol="BTCUSDT",
        start_ms=START,
        end_ms=END,
        interval_ms=60000,
        tick_size="0.5",
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

    from src.storage.migrations import migrate
    from src.storage.store import Store

    # DATA #72 is opt-in compositional source: resolve it after the pin gate,
    # without changing Ruff import groups based on its physical presence.
    fetch_and_ingest = importlib.import_module("src.ingest.public_auxiliary").fetch_and_ingest

    with psycopg.connect(dsn) as connection:
        migrate(connection)
    ms = 1600000060000
    original = [
        {
            "symbol": "BTCUSDT",
            "execId": "research-pg-side-buy-v2",
            "time": str(ms),
            "price": "100",
            "size": "0.5",
            "side": "Buy",
        },
        {
            "symbol": "BTCUSDT",
            "execId": "research-pg-side-unknown-v2",
            "time": str(ms + 1),
            "price": "101",
            "size": "0.25",
            "side": "Unknown",
        },
    ]
    result = {"list": original}
    params = {"category": "linear", "symbol": "BTCUSDT", "limit": 1000}
    url = "https://api.bybit.com/v5/market/recent-trade?" + urlencode(params)
    body = json.dumps({"retCode": 0, "result": result}).encode()
    sha = hashlib.sha256(body).hexdigest()

    class HttpProof:
        def __init__(self, *, tampered=False):
            self.last_response = {
                "url": url,
                "status_code": 200,
                "hash_scope": "original_http_body",
                "sha256": "0" * 64 if tampered else sha,
                "bytes_base64": base64.b64encode(body).decode(),
            }

        def call(self, path, got):
            assert path == "/recent-trade" and got == params
            return result, url

    kwargs = dict(
        kind="trades",
        category="linear",
        symbol="BTCUSDT",
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
            reopened,
            category="linear",
            symbol="BTCUSDT",
            start_ms=start,
            end_ms=start + 60000,
            interval_ms=60000,
            tick_size="0.5",
            limit=2,
        )
    # Inspect the actual migrated database, not just the Python normalizer.
    # Existing legacy price/size/exchange-ID checks must still be enforced.
    with reopened.connect() as connection:
        from psycopg.rows import dict_row, tuple_row

        query = """SELECT conname, pg_get_constraintdef(oid) AS definition
                   FROM pg_constraint
                   WHERE conrelid = 'tester_storage.raw_records'::regclass
                     AND conname IN ('raw_records_check2', 'trade_side_required')"""
        for row_factory in (dict_row, tuple_row):
            with connection.cursor(row_factory=row_factory) as cursor:
                cursor.execute(query)
                constraints = _constraint_definitions(cursor.fetchall())
            assert set(constraints) == {"raw_records_check2", "trade_side_required"}
            assert all("'Unknown'" in clause for clause in constraints.values())
            # Migration 008 must preserve identity, positive price/size and
            # constrained taker side; never weaken the SQL CHECKs.
            detailed = constraints["raw_records_check2"]
            for fragment in ("trade_id IS NOT NULL", "price >", "size >"):
                assert fragment in detailed
        persisted = connection.execute(
            """SELECT payload->>'side' AS side, price, size, trade_id
               FROM tester_storage.raw_records
               WHERE venue='bybit' AND category='linear'
                 AND symbol='BTCUSDT' AND dataset='trades'
                 AND trade_id='research-pg-side-unknown-v2'"""
        ).fetchone()
        assert persisted["side"] == "Unknown"
        assert str(persisted["price"]) == "101"
        assert str(persisted["size"]) == "0.25"
        assert persisted["trade_id"] == "research-pg-side-unknown-v2"
        # Historical rows are append-only: the BEFORE UPDATE trigger is
        # intentionally stronger than CHECK and runs first. Do not disable it
        # just to make the test reach a later constraint.
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            with connection.transaction():
                connection.execute(
                    "UPDATE tester_storage.raw_records "
                    "SET trade_id = NULL "
                    "WHERE trade_id = 'research-pg-side-unknown-v2'"
                )
        # Exercise the actual CHECKs separately with *new invalid INSERTs*.
        # Copy all other validated columns from an authentic row, supplying
        # a distinct key/trade_id to avoid unrelated uniqueness violations.
        negative_rows = (
            (
                "invalid_side",
                "jsonb_set(payload, '{side}', to_jsonb('Other'::text))",
                "research-negative-invalid-side",
            ),
            (
                "zero_size",
                "jsonb_set(payload, '{size}', to_jsonb('0'::text))",
                "research-negative-zero-size",
            ),
            (
                "zero_price",
                "jsonb_set(payload, '{price}', to_jsonb('0'::text))",
                "research-negative-zero-price",
            ),
            ("missing_trade_id", "payload", None),
        )
        for label, payload_expression, trade_identity in negative_rows:
            record_key = hashlib.sha256(
                ("research-pg-constraint-negative-" + label).encode()
            ).hexdigest()
            statement = (
                "INSERT INTO tester_storage.raw_records ("
                "record_key, venue, category, symbol, dataset, interval, "
                "schema_version, timestamp_ms, timestamp_ns, timestamp_us, "
                "timestamp_precision, trade_id, id_origin, payload, content_hash"
                ") SELECT %s, venue, category, symbol, dataset, interval, "
                "schema_version, timestamp_ms, timestamp_ns, timestamp_us, "
                "timestamp_precision, %s, id_origin, "
                + payload_expression
                + ", content_hash FROM tester_storage.raw_records "
                "WHERE trade_id = 'research-pg-side-unknown-v2'"
            )
            with pytest.raises(psycopg.errors.CheckViolation):
                with connection.transaction():
                    connection.execute(statement, (record_key, trade_identity))
        absent = connection.execute(
            "SELECT count(*) AS rejected_rows FROM tester_storage.raw_records "
            "WHERE trade_id LIKE 'research-negative-%'"
        ).fetchone()
        assert absent["rejected_rows"] == 0
        original = connection.execute(
            "SELECT payload->>'side' AS side, size, trade_id "
            "FROM tester_storage.raw_records "
            "WHERE trade_id = 'research-pg-side-unknown-v2'"
        ).fetchone()
        assert original["side"] == "Unknown"
        assert str(original["size"]) == "0.25"
        assert original["trade_id"] == "research-pg-side-unknown-v2"
    actual = read_data_metrics(
        reopened,
        category="linear",
        symbol="BTCUSDT",
        start_ms=start,
        end_ms=start + 60000,
        interval_ms=60000,
        tick_size="0.5",
        limit=3,
    )
    bar = actual["metrics"]["bars"][0]
    assert actual["observed_window"]["observed_unique_trades"] == 2
    ui = project_source_observation(actual)
    verified_ui = read_verified_ui_observation(
        reopened,
        category="linear",
        symbol="BTCUSDT",
        start_ms=start,
        end_ms=start + 60000,
        interval_ms=60000,
        tick_size="0.5",
        limit=3,
    )
    assert ui == verified_ui
    # Original event visibility cannot be moved backwards to permit lookahead.
    with pytest.raises(ValueError, match="late_source_knowledge"):
        read_verified_ui_observation(
            reopened,
            category="linear",
            symbol="BTCUSDT",
            start_ms=start,
            end_ms=start + 60000,
            interval_ms=60000,
            tick_size="0.5",
            limit=3,
            as_of_ms=start + 60000,
        )
    assert ui["bars"][0]["unknown_side_volume"] == "0.25"
    assert ui["bars"][0]["delta_known"] == "0.5"
    assert ui["source_verification"] == "DATA72_PERSISTED_HTTP_ROW_PAGE_RECEIPTS"
    assert ui["f_admission"] == "DENIED_UNKNOWN_COVERAGE"
    assert ui["g_e_execution"] == "NOT_AUTHORIZED"
    assert ui["historical_known_at_ms"] is None
    assert ui["http_route"] is None
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
    assert (
        len(
            reopened.read(
                "trades",
                "bybit",
                "linear",
                "BTCUSDT",
                start,
                start + 60000,
                "",
                limit=10,
            )
        )
        == 2
    )


def test_strategy_lab_f_rejects_malformed_window_without_attribute_error():
    preview = project_events(
        [event()],
        category="linear",
        symbol="BTCUSDT",
        start_ms=START,
        end_ms=END,
        interval_ms=60000,
        tick_size="0.5",
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

    from src.storage.migrations import migrate
    from src.storage.store import Store

    # DATA #72 is opt-in compositional source: resolve it after the pin gate,
    # without changing Ruff import groups based on its physical presence.
    fetch_and_ingest = importlib.import_module("src.ingest.public_auxiliary").fetch_and_ingest

    with psycopg.connect(dsn) as connection:
        migrate(connection)
    start = 1600000200000 // 60000 * 60000
    rows = [
        {
            "symbol": "BTCUSDT",
            "execId": "research-gap-first-v3",
            "time": str(start + 1000),
            "price": "100",
            "size": "1",
            "side": "Buy",
        },
        {
            "symbol": "BTCUSDT",
            "execId": "research-gap-third-v3",
            "time": str(start + 121000),
            "price": "102",
            "size": "1",
            "side": "Sell",
        },
    ]
    result = {"list": rows}
    params = {"category": "linear", "symbol": "BTCUSDT", "limit": 1000}
    url = "https://api.bybit.com/v5/market/recent-trade?" + urlencode(params)
    raw = json.dumps({"retCode": 0, "result": result}).encode()

    class Replay:
        last_response = {
            "url": url,
            "status_code": 200,
            "hash_scope": "original_http_body",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes_base64": base64.b64encode(raw).decode(),
        }

        def call(self, path, got):
            assert path == "/recent-trade" and got == params
            return result, url

    store = Store(dsn)
    fetch_and_ingest(
        store,
        Replay(),
        kind="trades",
        category="linear",
        symbol="BTCUSDT",
        run_id="research-gap-v3-run",
        job_key="research-gap-v3-job",
        observed_at=datetime(2026, 11, 13, 20, tzinfo=UTC),
    )
    reopened = Store(dsn)
    preview = read_data_metrics(
        reopened,
        category="linear",
        symbol="BTCUSDT",
        start_ms=start,
        end_ms=start + 180000,
        interval_ms=60000,
        tick_size="0.5",
        limit=3,
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


def _research_ui_contract_fixture():
    """Synthetic shape-only fixture; cannot certify actual DATA provenance."""
    source = preview(
        [
            event("ui-buy", price="100", quantity="2", side="Buy"),
            event("ui-sell", price="102", quantity="1", side="Sell"),
            event("ui-unknown", price="100", quantity="0.25", side="Unknown"),
        ]
    )
    source["source_verification"] = "DATA72_PERSISTED_HTTP_ROW_PAGE_RECEIPTS"
    source["persisted_dataset_revision"] = None
    source["historical_known_at_ms"] = None
    source["data_source_receipts_digest"] = digest(["test-shape-only"])
    source["observed_sample_fingerprint"] = digest({"test": "shape-only"})
    return source


def test_research_ui_observation_shape_preserves_exact_decimal_and_denies_f_g_e():
    source = _research_ui_contract_fixture()
    projected = project_source_observation(source)
    bar = projected["bars"][0]
    assert bar["volume"] == "3.25"
    assert bar["buy_volume"] == "2"
    assert bar["sell_volume"] == "1"
    assert bar["unknown_side_volume"] == "0.25"
    assert bar["delta_known"] == "1"
    assert bar["cvd_known"] == "1"
    assert bar["poc"] == "100"
    assert bar["vwap"].startswith("100.615384615")
    assert projected["unknown_side_present"] is True
    assert projected["f_admission"] == "DENIED_UNKNOWN_COVERAGE"
    assert projected["g_e_execution"] == "NOT_AUTHORIZED"
    assert projected["http_route"] is None
    assert projected["http_route_status"] == "NOT_REGISTERED"
    assert projected["historical_known_at_ms"] is None
    with pytest.raises(ValueError, match="invalid_research_preview_contract"):
        require_strategy_lab_f_input(projected)


def test_research_ui_refuses_unattested_or_missing_persisted_source():
    source = preview([event()])
    source["persisted_dataset_revision"] = None
    source["historical_known_at_ms"] = None
    with pytest.raises(ValueError, match="research_ui_not_source_backed"):
        project_source_observation(source)
    source = _research_ui_contract_fixture()
    source["source_verification"] = "CALLER_UNATTESTED"
    with pytest.raises(ValueError, match="research_ui_not_source_backed"):
        project_source_observation(source)


@pytest.mark.parametrize(
    "key,value",
    [
        ("coverage", "COMPLETE"),
        ("admitted_to_f", True),
        ("f_input", {"forged": True}),
        ("g_e_result", {"forged": True}),
        ("publication_time", "KNOWN"),
        ("persisted_dataset_revision", "fabricated"),
        ("historical_known_at_ms", START + 1000),
    ],
)
def test_research_ui_never_promotes_unknown_to_historical_authority(key, value):
    source = _research_ui_contract_fixture()
    source[key] = value
    with pytest.raises(ValueError, match="research_ui_historical_authority_refused"):
        project_source_observation(source)


def test_research_ui_rejects_mutated_metrics_with_valid_fingerprint():
    source = _research_ui_contract_fixture()
    source["metrics"]["bars"][0]["delta_known"] = "999"
    with pytest.raises(ValueError, match="research_ui_metric_digest_mismatch"):
        project_source_observation(source)
    source["metrics_digest"] = digest(source["metrics"])
    with pytest.raises(ValueError, match="research_ui_execution_volume_mismatch"):
        project_source_observation(source)


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("cvd_known", "999", "research_ui_cvd_anchor_mismatch"),
        ("poc", "102", "research_ui_poc_mismatch"),
        ("vwap", "-1", "research_ui_negative_volume"),
    ],
)
def test_research_ui_cannot_be_forged_by_rehashing_wrong_math(field, value, reason):
    source = _research_ui_contract_fixture()
    source["metrics"]["bars"][0][field] = value
    source["metrics_digest"] = digest(source["metrics"])
    with pytest.raises(ValueError, match=reason):
        project_source_observation(source)


def test_research_ui_refuses_malformed_footprint_even_if_digest_recalculated():
    source = _research_ui_contract_fixture()
    source["metrics"]["bars"][0]["footprint"][0]["unknown_side_volume"] = "4"
    source["metrics_digest"] = digest(source["metrics"])
    with pytest.raises(ValueError, match="research_ui_level_volume_mismatch"):
        project_source_observation(source)


def test_research_ui_cvd_window_and_utc_boundaries_are_not_lookahead_claims():
    source = _research_ui_contract_fixture()
    source["metrics"]["bars"][0]["start_ms"] += 1
    source["metrics_digest"] = digest(source["metrics"])
    with pytest.raises(ValueError, match="research_ui_noncontiguous_utc_bars"):
        project_source_observation(source)
    source = _research_ui_contract_fixture()
    source["observed_window"]["historical_completeness_state"] = "COMPLETE"
    with pytest.raises(ValueError, match="research_ui_coverage_unverified"):
        project_source_observation(source)


def test_research_ui_refuses_invalid_receipt_fingerprint():
    source = _research_ui_contract_fixture()
    source["data_source_receipts_digest"] = "not-an-http-hash"
    with pytest.raises(ValueError, match="research_ui_unqualified_hash"):
        project_source_observation(source)


def test_research_ui_verified_entrypoint_reads_composed_data_only():
    _composed_data()
    from tests.test_execution_contract_bridge import MS, compact_store, fixture

    start = MS // 60000 * 60000
    store = compact_store([fixture(side="Unknown")])
    observation = read_verified_ui_observation(
        store,
        category="linear",
        symbol="BTCUSDT",
        start_ms=start,
        end_ms=start + 60000,
        interval_ms=60000,
        tick_size="0.5",
        limit=10,
    )
    assert observation["bars"][0]["unknown_side_volume"] == "0.125"
    assert observation["method"] == "tester.execution-metrics/2"
    assert observation["metrics_contract"]["status"] == "CURRENT"
    assert observation["f_admission"] == "DENIED_UNKNOWN_COVERAGE"
    assert observation["g_e_execution"] == "NOT_AUTHORIZED"
    assert observation["http_route"] is None
    store.read_bounded.assert_called_once()
    store.ingest.assert_not_called()


# --- execution-metrics/2 consumers (issue #112 follow-up) -------------------
# Golden digests over the _research_ui_contract_fixture() executions.  The /1
# value was produced by the pre-migration adapter (base 8f6a85e) and must stay
# verifiable unchanged; the /2 value pins the new write contract.
GOLDEN_V1_METRICS_DIGEST = "8d83a9b26fd0825aa609dd246f47e028d4a9c7ec395c437edca8b5bd776d32b8"
GOLDEN_V2_METRICS_DIGEST = "ed12ad36cc7c78949df10d35ea173cda996a66cc3777557fabb296a6950046e0"
UI_EXEC_IDS = ("ui-buy", "ui-sell", "ui-unknown")


def _historical_v1_preview():
    """A persisted /1 preview exactly as the pre-migration adapter stored it."""
    from src.analytics.research_metrics import calculate
    from src.integration.data_research_adapter import _trade

    source = _research_ui_contract_fixture()
    trades = sorted(
        (
            _trade(
                e, category="linear", symbol="BTCUSDT", start_ms=START, end_ms=END, as_of_ms=None
            )
            for e in (
                event("ui-buy", price="100", quantity="2", side="Buy"),
                event("ui-sell", price="102", quantity="1", side="Sell"),
                event("ui-unknown", price="100", quantity="0.25", side="Unknown"),
            )
        ),
        key=lambda t: (t["timestamp_ms"], t["trade_id"]),
    )
    source["metrics"] = calculate(
        trades, start_ms=START, end_ms=END, interval_ms=60000, tick_size="0.5"
    )
    source["metrics_digest"] = digest(source["metrics"])
    return source


def test_new_previews_are_written_with_execution_metrics_v2_golden():
    # Before the migration project_events wrote tester.execution-metrics/1
    # (digest GOLDEN_V1_METRICS_DIGEST, no quantity_unit) and the projection
    # had no metrics_contract: both assertions below failed.
    source = _research_ui_contract_fixture()
    assert source["metrics"]["method"] == "tester.execution-metrics/2"
    assert source["metrics"]["quantity_unit"] == "base"
    assert source["quantity_unit"] == "source_native_linear_size"
    assert source["metrics_digest"] == GOLDEN_V2_METRICS_DIGEST
    projected = project_source_observation(source)
    assert projected["metrics_contract"] == {
        "method": "tester.execution-metrics/2",
        "quantity_unit": "base",
        "status": "CURRENT",
    }
    assert projected["bars"][0]["vwap"].startswith("100.615384615")


def test_historical_v1_preview_stays_verifiable_read_only_with_unchanged_digest():
    stored = _historical_v1_preview()
    assert stored["metrics"]["method"] == "tester.execution-metrics/1"
    assert "quantity_unit" not in stored["metrics"]
    assert stored["metrics_digest"] == GOLDEN_V1_METRICS_DIGEST
    projected = project_source_observation(stored)
    assert projected["method"] == "tester.execution-metrics/1"
    assert projected["metrics_digest"] == GOLDEN_V1_METRICS_DIGEST
    assert projected["metrics_contract"] == {
        "method": "tester.execution-metrics/1",
        "quantity_unit": "base",
        "status": "HISTORICAL_READ_ONLY",
    }
    # Never silently reinterpreted: relabelling /1 as /2 breaks the digest,
    # and a re-hashed relabel without quantity_unit is refused.
    relabelled = _historical_v1_preview()
    relabelled["metrics"]["method"] = "tester.execution-metrics/2"
    with pytest.raises(ValueError, match="research_ui_metric_digest_mismatch"):
        project_source_observation(relabelled)
    relabelled["metrics_digest"] = digest(relabelled["metrics"])
    with pytest.raises(
        ValueError, match="research_ui_metric_authority_refused:v2_quantity_unit_mismatch"
    ):
        project_source_observation(relabelled)
    # /1 never carried v2 fields.
    hybrid = _historical_v1_preview()
    hybrid["metrics"]["quantity_unit"] = "base"
    hybrid["metrics_digest"] = digest(hybrid["metrics"])
    with pytest.raises(ValueError, match="v1_metrics_with_v2_fields"):
        project_source_observation(hybrid)


def _inverse_event(trade_id, *, price, quantity, side):
    inverse = event(trade_id, price=price, quantity=quantity, side=side)
    inverse.update(
        category="inverse", symbol="BTCUSD", quantity_unit="source_native_inverse_contract"
    )
    return inverse


INVERSE_EVENTS = (
    ("inv-a", "100", "100", "Buy"),
    ("inv-b", "200", "100", "Sell"),
)


def _inverse_events():
    return [_inverse_event(i, price=p, quantity=q, side=s) for i, p, q, s in INVERSE_EVENTS]


def _expected_inverse_vwap():
    from fractions import Fraction

    from src.analytics.exact import decimal

    return decimal(Fraction(400, 3))


def test_inverse_v2_vwap_through_adapter_and_ui_projection():
    # Before: _scope refused every inverse request
    # ("inverse_quantity_unit_not_comparable") and /1 could not compute it.
    source = project_events(
        _inverse_events(),
        category="inverse",
        symbol="BTCUSD",
        start_ms=START,
        end_ms=END,
        interval_ms=60000,
        tick_size="1",
    )
    assert source["quantity_unit"] == "source_native_inverse_contract"
    metrics = source["metrics"]
    assert metrics["method"] == "tester.execution-metrics/2"
    assert metrics["quantity_unit"] == "quote"
    bar = metrics["bars"][0]
    # 100 contracts @100 + 100 contracts @200 -> 200 / (1 + 0.5) = 400/3.
    assert bar["vwap"] == _expected_inverse_vwap()
    assert bar["vwap"] != "150"
    assert bar["volume"] == "200"
    assert bar["delta_known"] == "0"
    source["source_verification"] = "DATA72_PERSISTED_HTTP_ROW_PAGE_RECEIPTS"
    source["persisted_dataset_revision"] = None
    source["historical_known_at_ms"] = None
    source["data_source_receipts_digest"] = digest(["test-shape-only"])
    source["observed_sample_fingerprint"] = digest({"test": "shape-only"})
    projected = project_source_observation(source)
    assert projected["bars"][0]["vwap"] == _expected_inverse_vwap()
    assert projected["metrics_contract"] == {
        "method": "tester.execution-metrics/2",
        "quantity_unit": "quote",
        "status": "CURRENT",
    }
    # The preview's source unit must match the metrics market category.
    source["quantity_unit"] = "source_native_linear_size"
    with pytest.raises(ValueError, match="research_ui_metric_quantity_unit_mismatch"):
        project_source_observation(source)
    # A /1 inverse result (old arithmetic VWAP) stays refused, never upgraded.
    source["quantity_unit"] = "source_native_inverse_contract"
    source["metrics"]["method"] = "tester.execution-metrics/1"
    del source["metrics"]["quantity_unit"]
    source["metrics_digest"] = digest(source["metrics"])
    with pytest.raises(ValueError, match="v1_inverse_vwap_invalid_recompute_as_v2"):
        project_source_observation(source)


def test_inverse_event_unit_must_be_inverse_contract():
    wrong = _inverse_event("inv-a", price="100", quantity="100", side="Buy")
    wrong["quantity_unit"] = "source_native_linear_size"
    with pytest.raises(ValueError, match="market_or_quantity_unit_mismatch"):
        project_events(
            [wrong],
            category="inverse",
            symbol="BTCUSD",
            start_ms=START,
            end_ms=END,
            interval_ms=60000,
            tick_size="1",
        )
    with pytest.raises(ValueError, match="execution_category_not_comparable"):
        project_events(
            [],
            category="spot",
            symbol="BTCUSDT",
            start_ms=START,
            end_ms=END,
            interval_ms=60000,
            tick_size="1",
        )


def test_inverse_read_path_when_data_declares_inverse_projection(monkeypatch):
    """read_data_metrics -> read_verified_ui_observation for inverse, DATA stubbed.

    The real DATA module declares the same projection (see
    test_real_data_declares_unit_matched_inverse_projection); the real
    PostgreSQL chain is exercised in test_runtime_privileges_issue114.
    """
    from types import SimpleNamespace

    import src.integration.data_research_adapter as adapter

    events = _inverse_events()

    def to_research_trade(e):
        return adapter._trade(
            e, category="inverse", symbol="BTCUSD", start_ms=START, end_ms=END, as_of_ms=None
        )

    store = Mock()
    fake = SimpleNamespace(
        VERSION=DATA_CONTRACT_VERSION,
        RESEARCH_CATEGORIES=("linear", "inverse"),
        SOURCE_QUANTITY_UNITS=dict(adapter.SOURCE_QUANTITY_UNITS),
        read_execution_events=lambda store, **kw: (store.read_bounded(**kw), events)[1],
        to_research_trade=to_research_trade,
    )
    monkeypatch.setattr(adapter.importlib, "import_module", lambda name: fake)
    observation = read_verified_ui_observation(
        store,
        category="inverse",
        symbol="BTCUSD",
        start_ms=START,
        end_ms=END,
        interval_ms=60000,
        tick_size="1",
        limit=10,
    )
    assert observation["bars"][0]["vwap"] == _expected_inverse_vwap()
    assert observation["metrics_contract"]["quantity_unit"] == "quote"
    assert observation["quantity_unit"] == "quote"
    assert observation["source_quantity_unit"] == "source_native_inverse_contract"
    assert observation["vwap_unit"] == "quote_per_base"
    assert observation["f_admission"] == "DENIED_UNKNOWN_COVERAGE"
    store.read_bounded.assert_called_once()
    store.ingest.assert_not_called()


def test_inverse_still_refused_before_read_without_data_declaration(monkeypatch):
    from types import SimpleNamespace

    import src.integration.data_research_adapter as adapter

    monkeypatch.setattr(
        adapter.importlib,
        "import_module",
        lambda name: SimpleNamespace(VERSION=DATA_CONTRACT_VERSION),
    )
    store = Mock()
    with pytest.raises(ValueError, match="inverse_quantity_unit_not_comparable"):
        read_data_metrics(
            store,
            category="inverse",
            symbol="BTCUSD",
            start_ms=START,
            end_ms=END,
            interval_ms=60000,
            tick_size="1",
        )
    store.read_bounded.assert_not_called()


def test_mixed_metrics_versions_in_one_response_are_explicit():
    from src.integration.research_ui_projection import combine_observations

    current = project_source_observation(_research_ui_contract_fixture())
    historical = project_source_observation(_historical_v1_preview())
    bundle = combine_observations([current, historical])
    assert bundle["mixed_metrics_versions"] is True
    assert bundle["metrics_methods"] == ["tester.execution-metrics/1", "tester.execution-metrics/2"]
    assert bundle["historical_read_only_count"] == 1
    assert [o["metrics_contract"]["status"] for o in bundle["observations"]] == [
        "CURRENT",
        "HISTORICAL_READ_ONLY",
    ]
    single = combine_observations([current])
    assert single["mixed_metrics_versions"] is False
    unlabelled = dict(current)
    del unlabelled["metrics_contract"]
    with pytest.raises(ValueError, match="research_ui_observation_contract"):
        combine_observations([unlabelled])


def test_capability_contracts_advertise_v2_with_v1_read_only():
    from src.analytics.research_metrics import CURRENT_METHOD, METHOD_V1
    from src.api.capability_source_contracts import RESEARCH_METRICS_VERSION, manifest

    assert RESEARCH_METRICS_VERSION == CURRENT_METHOD == "tester.execution-metrics/2"
    contracts = manifest()["source_contracts"]
    metrics = contracts["observed_execution_metrics"]
    assert metrics["contract_version"] == metrics["write_version"] == CURRENT_METHOD
    assert metrics["read_compatible_versions"] == {
        CURRENT_METHOD: "CURRENT",
        METHOD_V1: "HISTORICAL_READ_ONLY",
    }
    assert metrics["historical_inverse"] == "REFUSED"
    assert metrics["quantity_unit"]["inverse"] == "quote"
    assert metrics["source_backed_categories"] == ["inverse", "linear"]
    assert metrics["vwap_formula"]["inverse"] == "sum(q)/sum(q/p)"
    assert metrics["mixed_unit_aggregation"] == "REFUSED"
    executions = contracts["public_executions"]
    assert executions["quantity_unit"] == {
        "linear": "source_native_linear_size",
        "inverse": "source_native_inverse_contract",
    }
    assert executions["categories"] == ["inverse", "linear"]
    assert CURRENT_METHOD in contracts["research_ui_observation"]["requires"]
    assert METHOD_V1 not in contracts["research_ui_observation"]["requires"]
