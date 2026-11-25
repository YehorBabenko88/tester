"""Behaviour-equivalence regressions for the root prototype removed in PR #102.

Each test ports a case from the deleted root modules or tests
(``git show d6059af:bybit.py``, ``engine.py``, ``db.py``, ``bybit_history.py``,
``test_core.py``, ``test_ingest_integrity.py``, ``test_postgres_integration.py``,
``test_screener.py``) to the canonical ``src/ingest``, ``src/storage`` or
``src/analytics`` implementation.  The mapping and the behaviours without an
equivalent (de-scope proposals) are listed in docs/release/consolidation.md.
"""

import gzip
import json
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.analytics.core import deduplicate, interval_bounds, normalize_trade, validate_trade
from src.ingest.archive import archive_url, iter_trade_records, timestamp_fields
from src.ingest.catalog import instruments
from src.ingest.journal import Journal
from src.ingest.rest import API, Backfill, expected_times, next_time
from src.storage.models import normalize

DAY = 86400000


def ms(y, m, d, h=0, minute=0):
    return int(datetime(y, m, d, h, minute, tzinfo=UTC).timestamp() * 1000)


class FakeBybit:
    """Emulates /v5/market list semantics: newest ``limit`` rows in [start,end]."""

    def __init__(self, rows, *, field=None, server_ms=10**13):
        self.rows, self.field, self.server_ms = rows, field, server_ms
        self.calls, self.returned = [], []

    def server_time_ms(self):
        return self.server_ms

    def call(self, path, params):
        self.calls.append((path, dict(params)))
        a, b = (
            (params["start"], params["end"])
            if self.field is None
            else (params["startTime"], params["endTime"])
        )

        def ts(r):
            return int(r[0] if self.field is None else r[self.field])

        hits = sorted((r for r in self.rows if a <= ts(r) <= b), key=ts, reverse=True)
        self.returned.append(len(hits[: params["limit"]]))
        return {"list": hits[: params["limit"]]}, "https://api.bybit.com/v5/market" + path


def candle(ts, o="10", h="11", low="9", c="10"):
    return [str(ts), o, h, low, c, "5", "50"]


@pytest.fixture
def journal(tmp_path):
    j = Journal(tmp_path / "journal.db")
    yield j
    j.close()


# --- bybit.py / test_core.py BoundaryTests ---------------------------------


def test_open_minute_candle_is_not_published_closed_before(journal):
    # closed_before("1", 12:32:32) == 12:32: the 12:32 candle is still open.
    now = ms(2026, 11, 7, 12, 32) + 32000
    rows = [candle(ms(2026, 11, 7, 12, 31)), candle(ms(2026, 11, 7, 12, 32))]
    api = FakeBybit(rows, server_ms=now)
    out = Backfill(api, journal).run(
        "linear", "BTCUSDT", "kline", "1", ms(2026, 11, 7, 12, 31), ms(2026, 11, 7, 12, 33)
    )
    assert out["records"] == 1
    assert out["status"] == "unverified"


def test_week_buckets_are_monday_utc():
    # closed_before("W", 2026-11-07 12:00) == Monday 2026-11-02.
    assert interval_bounds(ms(2026, 11, 7, 12), "W")[0] == ms(2026, 11, 2)
    assert list(expected_times(ms(2026, 11, 1), ms(2026, 11, 10), "W")) == [
        ms(2026, 11, 2),
        ms(2026, 11, 9),
    ]


def test_month_bucket_leap_year():
    assert interval_bounds(ms(2024, 2, 29, 23), "M")[0] == ms(2024, 2, 1)
    assert next_time(ms(2024, 2, 1), "M") == ms(2024, 3, 1)


@pytest.mark.parametrize("bad", ["../etc", "btc usdt", "x/../../", ""])
def test_unsafe_symbols_rejected(bad):
    assert archive_url("trading", "BTCUSDT", "BTCUSDT2024-01-01.csv.gz").endswith(
        "/trading/BTCUSDT/BTCUSDT2024-01-01.csv.gz"
    )
    with pytest.raises(ValueError):
        archive_url("trading", bad, "BTCUSDT2024-01-01.csv.gz")
    trade = normalize_trade(
        {"time": "1", "price": "1", "size": "1", "side": "Buy", "execId": "a"},
        category="linear",
        symbol="BTCUSDT",
        source_ref={
            "source_kind": "bybit_rest",
            "source_ref": "r",
            "source_hash": None,
            "ingestion_run": "run",
            "timestamp_precision": "ms",
            "source_timestamp": "1",
        },
    )
    with pytest.raises(ValueError):
        validate_trade({**trade, "symbol": bad})


def test_busy_one_minute_series_never_truncated_by_1000_row_limit(journal):
    # candle_slices kept every request below 1000 rows; canonical pagination
    # walks the cursor end backwards and returns the complete 2-day series.
    start, end = ms(2026, 11, 1), ms(2026, 11, 3)
    rows = [candle(t) for t in range(start, end, 60000)]
    api = FakeBybit(rows)
    out = Backfill(api, journal).run("linear", "BTCUSDT", "kline", "1", start, end)
    assert out["records"] == 2880
    assert out["status"] == "observed"
    assert all(q["limit"] <= 1000 for _, q in api.calls)
    assert max(api.returned) <= 1000 and len(api.calls) >= 3


def test_trade_archive_timestamp_eras():
    assert timestamp_fields("1700000000.123")[:1] == (1700000000123,)
    assert timestamp_fields("1700000000123") == (1700000000123, None, "ms")
    assert timestamp_fields("1700000000123456") == (1700000000123, None, "us")
    assert timestamp_fields("1700000000123456789") == (1700000000123, 1700000000123456789, "ns")


def test_only_public_market_endpoints_are_reachable():
    transport = Mock()
    with pytest.raises(ValueError):
        API(transport).call("/../order/create", {})
    with pytest.raises(ValueError):
        API(transport).call("order/create", {})
    transport.get.assert_not_called()


# --- engine.py candles / test_core.py IntegrityTests -----------------------


@pytest.mark.parametrize(
    "present,length", [((0, 120000), 180000), ((0,), 120000)], ids=["internal", "trailing"]
)
def test_missing_minute_is_a_gap_not_complete(journal, present, length):
    start = ms(2026, 11, 1)
    api = FakeBybit([candle(start + offset) for offset in present], server_ms=ms(2026, 11, 7))
    out = Backfill(api, journal).run("linear", "BTCUSDT", "kline", "1", start, start + length)
    assert out["status"] == "unverified"
    assert out["observations"][0]["missing_count"] == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"open": "10", "high": "9", "low": "8", "close": "10"},
        {"open": "10", "high": "11", "low": "10.5", "close": "10"},
        {"open": "-1", "high": "11", "low": "-2", "close": "10"},
        {"open": "10", "high": "11", "low": "9", "close": "10", "volume": "-1"},
        {"open": "10", "high": "11", "low": "9", "close": "10", "turnover": "-1"},
        {"open": "NaN", "high": "11", "low": "9", "close": "10"},
    ],
)
def test_invalid_ohlc_volume_turnover_rejected(payload):
    record = storage_record("candles", "1", ms(2026, 11, 1), payload)
    with pytest.raises(ValueError):
        normalize(record)


def test_premium_and_spot_dataset_rules(journal):
    api = FakeBybit([])
    with pytest.raises(ValueError):
        Backfill(api, journal).run("inverse", "BTCUSD", "premium", "1", 0, 60000)
    with pytest.raises(ValueError):
        Backfill(api, journal).run("spot", "BTCUSDT", "mark", "1", 0, 60000)
    assert api.calls == []


# --- engine.py observations / test_ingest_integrity.py ObservationIntegrity -


def storage_record(dataset, interval, ts, payload):
    return {
        "schema_version": "1.0",
        "venue": "bybit",
        "category": "linear",
        "symbol": "BTCUSDT",
        "dataset": dataset,
        "interval": interval,
        "timestamp_ms": ts,
        "payload": payload,
        "source": {
            "source_kind": "bybit_rest",
            "url": "https://api.bybit.com/v5/market/x",
            "fetched_at": "2026-11-07T12:00:00Z",
        },
    }


@pytest.mark.parametrize(
    "kind,field", [("open_interest", "openInterest"), ("long_short_ratio", "buyRatio")]
)
def test_sparse_regular_observations_stay_unverified(journal, kind, field):
    start = ms(2026, 11, 1)
    api = FakeBybit([{"timestamp": str(start), field: "0.5"}], field="timestamp")
    out = Backfill(api, journal).run("linear", "BTCUSDT", kind, "5min", start, start + DAY)
    assert out["status"] == "unverified"
    assert out["observations"][0]["missing_count"] == 287


def test_full_regular_observation_history_is_observed(journal):
    start = ms(2026, 11, 1)
    items = [{"timestamp": str(t), "buyRatio": "0.5"} for t in range(start, start + DAY, 300000)]
    out = Backfill(api := FakeBybit(items, field="timestamp"), journal).run(
        "linear", "BTCUSDT", "long_short_ratio", "5min", start, start + DAY
    )
    assert api.calls
    assert out["status"] == "observed"
    assert out["observations"][0]["missing_count"] == 0
    # Observed is never promoted to complete coverage.
    assert out["observations"][0]["coverage"]["status"] == "PARTIAL"


def test_off_grid_observation_does_not_satisfy_the_sampling_grid(journal):
    # Legacy test_off_grid_timestamp_rejected: state 'error', nothing saved.
    start = ms(2026, 11, 1)
    api = FakeBybit([{"timestamp": str(start + 1), "openInterest": "1"}], field="timestamp")
    with pytest.raises(ValueError, match="interval grid"):
        Backfill(api, journal).run(
            "linear", "BTCUSDT", "open_interest", "5min", start, start + 600000
        )
    assert journal.window_records("linear", "BTCUSDT", "open_interest", "5min", 0, 2**62) == {}


@pytest.mark.parametrize(
    "dataset,value",
    [("open_interest", "Infinity"), ("open_interest", "-1"), ("ratio", "1.01"), ("ratio", "NaN")],
)
def test_invalid_observation_values_rejected(dataset, value):
    with pytest.raises(ValueError):
        normalize(storage_record(dataset, "5min", ms(2026, 11, 1), {"value": value}))


def test_funding_is_never_promoted_to_complete(journal):
    start = ms(2026, 11, 1)
    api = FakeBybit(
        [{"fundingRateTimestamp": str(start), "fundingRate": "0.01"}],
        field="fundingRateTimestamp",
    )
    out = Backfill(api, journal).run("linear", "BTCUSDT", "funding", None, start, start + DAY)
    assert out["observations"][0]["coverage"]["status"] != "COMPLETE"
    assert (
        normalize(storage_record("funding", "", start, {"value": "-0.01"}))["payload"]["value"]
        == "-0.01"
    )


def test_previous_unproven_window_is_requeried(journal):
    start = ms(2026, 11, 1)
    key = "rest-v3:" + __import__("json").dumps(
        ["linear", "BTCUSDT", "open_interest", "5min", start, start + DAY]
    )
    journal.save(key, "observed", {"records": 0})  # legacy "complete" without evidence
    api = FakeBybit([], field="timestamp")
    Backfill(api, journal).run("linear", "BTCUSDT", "open_interest", "5min", start, start + DAY)
    assert api.calls


# --- engine.py archives / test_ingest_integrity.py ArchiveIntegrity ---------


def archive(tmp, body):
    path = Path(tmp) / "BTCUSDT2025-11-01.csv.gz"  # rows below are 2025-11-01 UTC
    path.write_bytes(body)
    return path


def test_corrupt_gzip_publishes_no_record():
    good = gzip.compress(
        b"timestamp,symbol,side,size,price,trdMatchID\n1761955200.1,BTCUSDT,Buy,1,2,a\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = archive(tmp, good[:-6])
        with pytest.raises((OSError, EOFError, ValueError)):
            next(iter_trade_records(path, "linear", "BTCUSDT", {"url": "u"}))


def test_nonfinite_or_nonpositive_trade_rejected():
    base = {"trade_id": "a", "price": "1", "size": "1", "side": "Buy"}
    for change in ({"price": "NaN"}, {"size": "0"}, {"price": "-1"}, {"size": "Infinity"}):
        with pytest.raises(ValueError):
            normalize(storage_record("trades", "", 1, {**base, **change}))


def test_missing_trade_id_requires_explicit_composite_opt_in():
    body = gzip.compress(b"timestamp,symbol,side,size,price\n1761955200.1,BTCUSDT,Buy,1,2\n")
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(ValueError, match="trade ID"):
            list(iter_trade_records(archive(tmp, body), "linear", "BTCUSDT", {"url": "u"}))


def test_changed_payload_for_same_trade_id_fails_closed():
    source = {
        "source_kind": "bybit_rest",
        "source_ref": "r",
        "source_hash": None,
        "ingestion_run": "run",
        "timestamp_precision": "ms",
        "source_timestamp": "1",
    }

    def t(price):
        return normalize_trade(
            {"time": "1", "price": price, "size": "1", "side": "Buy", "execId": "same"},
            category="linear",
            symbol="BTCUSDT",
            source_ref=source,
        )

    assert len(deduplicate([t("1"), t("1")])) == 1
    with pytest.raises(ValueError):
        deduplicate([t("1"), t("2")])
    a = normalize(
        storage_record("trades", "", 1, {"trade_id": "x", "price": "1", "size": "1", "side": "Buy"})
    )
    b = normalize(
        storage_record("trades", "", 1, {"trade_id": "x", "price": "2", "size": "1", "side": "Buy"})
    )
    # Same identity, different content: storage reports a conflict, never overwrites.
    assert a["record_key"] == b["record_key"] and a["content_hash"] != b["content_hash"]


# --- db.py / test_postgres_integration.py numeric exactness ----------------


def test_decimal_exactness_preserved():
    record = normalize(
        storage_record(
            "trades",
            "",
            1,
            {
                "trade_id": "x",
                "price": "123.000000000000000001",
                "size": "0.00000001",
                "side": "Buy",
            },
        )
    )
    assert record["payload"]["price"] == "123.000000000000000001"
    assert Decimal(record["payload"]["size"]) == Decimal("0.00000001")


# --- bybit.py instruments ---------------------------------------------------


def test_instrument_pagination_rejects_repeated_cursor():
    api = Mock()
    api.call.side_effect = [
        ({"list": [{"symbol": "A"}], "nextPageCursor": "c1"}, "u"),
        ({"list": [{"symbol": "B"}], "nextPageCursor": "c1"}, "u"),
    ]
    with pytest.raises(ValueError, match="Repeated"):
        list(instruments(api, "linear"))
    assert api.call.call_args_list[0].args[1]["limit"] == 1000


# --- Former gaps GAP-1..3, implemented on owner decision (#113) -------------
# Each fails the affected window/file like the legacy code did (state 'error').


def test_gap_off_grid_observation_with_full_grid_is_rejected(journal):
    start = ms(2026, 11, 1)
    rows = [{"timestamp": str(t), "openInterest": "1"} for t in (start, start + 300000)]
    api = FakeBybit(rows + [{"timestamp": str(start + 1), "openInterest": "1"}], field="timestamp")
    with pytest.raises(ValueError, match="interval grid"):
        Backfill(api, journal).run(
            "linear", "BTCUSDT", "open_interest", "5min", start, start + 600000
        )
    key = "rest-v3:" + json.dumps(
        ["linear", "BTCUSDT", "open_interest", "5min", start, start + 600000]
    )
    assert journal.task(key)["status"] == "failed"
    assert journal.window_records("linear", "BTCUSDT", "open_interest", "5min", 0, 2**62) == {}


def test_gap_conflicting_duplicate_candle_timestamp_is_rejected(journal):
    start = ms(2026, 11, 1)
    rows = [candle(start), candle(start, h="12"), candle(start + 60000)]
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        Backfill(FakeBybit(rows), journal).run(
            "linear", "BTCUSDT", "kline", "1", start, start + 120000
        )
    key = "rest-v3:" + json.dumps(["linear", "BTCUSDT", "kline", "1", start, start + 120000])
    assert journal.task(key)["status"] == "failed"
    assert journal.window_records("linear", "BTCUSDT", "kline", "1", 0, 2**62) == {}


def test_gap_archive_trade_outside_file_utc_date_is_rejected():
    body = gzip.compress(
        b"timestamp,symbol,side,size,price,trdMatchID\n"
        b"1761955200.1,BTCUSDT,Buy,1,2,a\n1700000000.1,BTCUSDT,Buy,1,2,b\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = archive(tmp, body)  # named 2025-11-01, second row is 2023-11-14
        records = iter_trade_records(path, "linear", "BTCUSDT", {"url": "u"})
        with pytest.raises(ValueError, match="outside archive UTC date"):
            next(records)  # whole file rejected before the in-date first row is yielded


# --- engine.build_clusters / db.footprint / db.cluster_totals --------------


def test_footprint_clusters_match_legacy_exact_price_delta():
    from fractions import Fraction

    from src.analytics.research_metrics import calculate

    source = {
        "source_kind": "bybit_rest",
        "source_ref": "r",
        "source_hash": None,
        "ingestion_run": "run",
        "timestamp_precision": "ms",
        "source_timestamp": "1",
    }
    raw = [
        (1, "100.5", "2", "Buy"),
        (2, "100.5", "0.5", "Sell"),
        (3, "101", "1", "Sell"),
        (60001, "100.5", "3", "Buy"),
    ]
    trades = [
        normalize_trade(
            {"time": str(t), "price": p, "size": q, "side": s, "execId": f"c{t}"},
            category="linear",
            symbol="BTCUSDT",
            source_ref=source,
        )
        for t, p, q, s in raw
    ]
    bars = calculate(trades, start_ms=0, end_ms=120000, interval_ms=60000, tick_size="0.5")["bars"]
    # Legacy SQL: per (bucket, exact price) sum(size) FILTER side, count(*).
    legacy = {}
    for t, p, q, s in raw:
        slot = legacy.setdefault((t // 60000 * 60000, Fraction(p)), [Fraction(), Fraction()])
        slot[0 if s == "Buy" else 1] += Fraction(q)
    canonical = {
        (bar["start_ms"], Fraction(level["price"])): [
            Fraction(level["buy_volume"]),
            Fraction(level["sell_volume"]),
        ]
        for bar in bars
        for level in bar["footprint"]
    }
    assert canonical == legacy
    totals = [(b["start_ms"], Fraction(b["delta_known"]), b["trade_count"]) for b in bars]
    assert totals == [(0, Fraction(1, 2), 3), (60000, Fraction(3), 1)]
    # Legacy only aggregated complete archive days; canonical never certifies.
    assert all(b["coverage_certified"] is False for b in bars)
