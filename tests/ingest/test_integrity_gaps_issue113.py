"""Regressions for the #113 ingest gaps GAP-1..3 (owner decision 2026-11-25).

GAP-1  an observation off the dataset's UTC interval grid fails the window.
GAP-2  a timestamp repeated with a different payload fails the window; identical
       repeats are deduplicated, never overwritten.
GAP-3  archive trades must lie in the UTC date encoded in the archive name.
"""

import gzip
import json
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from src.ingest.archive import archive_utc_day, iter_trade_records
from src.ingest.journal import Journal
from src.ingest.rest import Backfill, on_grid

URL = "https://api.bybit.com/v5/market/x"


def ms(*args):
    return int(datetime(*args, tzinfo=UTC).timestamp() * 1000)


START = ms(2026, 11, 2)  # a Monday


def candle(ts, h="11"):
    return [str(ts), "10", h, "9", "10", "5", "50"]


@pytest.fixture
def journal(tmp_path):
    j = Journal(tmp_path / "journal.db")
    yield j
    j.close()


def api_with(*pages, server_ms=10**13):
    api = Mock()
    api.server_time_ms.return_value = server_ms
    api.call.side_effect = [(page, URL) for page in pages]
    return api


def key(kind, interval, lo, hi):
    return "rest-v3:" + json.dumps(["linear", "BTCUSDT", kind, interval, lo, hi])


def stored(journal, kind, interval):
    return journal.window_records("linear", "BTCUSDT", kind, interval, 0, 2**62)


# --- GAP-1 -------------------------------------------------------------------


def test_on_grid_follows_the_expected_times_grid():
    assert on_grid(START, "5min") and not on_grid(START + 1, "5min")
    assert on_grid(START, "W") and not on_grid(START + 86400000, "W")
    assert on_grid(ms(2026, 11, 1), "M") and not on_grid(START, "M")
    assert on_grid(START + 43200000, "720") and not on_grid(START + 3600000, "720")


def test_account_ratio_off_grid_row_next_to_full_grid_fails_window(journal):
    rows = [{"timestamp": str(START + d), "buyRatio": "0.5"} for d in (300000, 1, 0)]
    api = api_with({"list": rows})
    with pytest.raises(ValueError, match="interval grid"):
        Backfill(api, journal).run(
            "linear", "BTCUSDT", "long_short_ratio", "5min", START, START + 600000
        )
    assert journal.task(key("long_short_ratio", "5min", START, START + 600000))["status"] == (
        "failed"
    )
    assert stored(journal, "long_short_ratio", "5min") == {}


def test_full_on_grid_ratio_window_is_still_observed(journal):
    rows = [{"timestamp": str(START + d), "buyRatio": "0.5"} for d in (300000, 0)]
    out = Backfill(api_with({"list": rows}), journal).run(
        "linear", "BTCUSDT", "long_short_ratio", "5min", START, START + 600000
    )
    assert out["status"] == "observed" and out["records"] == 2


def test_off_grid_daily_candle_fails_window(journal):
    api = api_with({"list": [candle(START + 3600000)]})
    with pytest.raises(ValueError, match="interval grid"):
        Backfill(api, journal).run("linear", "BTCUSDT", "kline", "D", START, START + 86400000)
    assert stored(journal, "kline", "D") == {}


def test_funding_has_no_grid_and_keeps_its_schedule_free_timestamps(journal):
    api = api_with({"list": [{"fundingRateTimestamp": str(START + 7), "fundingRate": "1"}]})
    out = Backfill(api, journal).run("linear", "BTCUSDT", "funding", "", START, START + 100)
    assert out["records"] == 1


# --- GAP-2 -------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["kline", "mark", "index", "premium"])
def test_conflicting_duplicate_candle_fails_window_for_every_kline_dataset(journal, kind):
    rows = [candle(START + 60000), candle(START, h="12"), candle(START)]
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        Backfill(api_with({"list": rows}), journal).run(
            "linear", "BTCUSDT", kind, "1", START, START + 120000
        )
    assert journal.task(key(kind, "1", START, START + 120000))["status"] == "failed"
    assert stored(journal, kind, "1") == {}


def test_identical_duplicate_candle_is_deduplicated(journal):
    rows = [candle(START + 60000), candle(START), candle(START)]
    out = Backfill(api_with({"list": rows}), journal).run(
        "linear", "BTCUSDT", "kline", "1", START, START + 120000
    )
    assert out["status"] == "observed" and out["records"] == 2
    assert stored(journal, "kline", "1")[START]["payload"] == candle(START)


def test_conflicting_open_interest_duplicate_fails_window(journal):
    rows = [{"timestamp": str(START), "openInterest": v} for v in ("1", "2")]
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        Backfill(api_with({"list": rows}), journal).run(
            "linear", "BTCUSDT", "open_interest", "5min", START, START + 300000
        )


def test_conflict_across_pages_of_one_window_is_not_overwritten(journal):
    first = {"list": [{"fundingRateTimestamp": str(START), "fundingRate": "1"}]}
    second = {"list": [{"fundingRateTimestamp": str(START), "fundingRate": "2"}]}
    api = api_with({**first, "nextPageCursor": "c1"}, second)
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        Backfill(api, journal).run("linear", "BTCUSDT", "funding", "", START, START + 100)
    assert stored(journal, "funding", "") == {}
    assert journal.task(key("funding", "", START, START + 100))["status"] == "failed"
    # The HTTP receipts remain auditable without publishing the rejected rows.
    assert journal.db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] >= 2


@pytest.mark.parametrize("kind, interval", [("kline", "1"), ("open_interest", "5min")])
def test_failed_later_page_never_exposes_earlier_valid_rows(journal, kind, interval):
    if kind == "kline":
        first = {"list": [candle(START)]}
        second = {"list": [candle(START, h="12")]}
    else:
        first = {"list": [{"timestamp": str(START), "openInterest": "1"}]}
        second = {"list": [{"timestamp": str(START), "openInterest": "2"}]}
    api = api_with({**first, "nextPageCursor": "page2"}, second)
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        Backfill(api, journal).run(
            "linear", "BTCUSDT", kind, interval, START,
            START + (60000 if kind == "kline" else 300000),
        )
    assert stored(journal, kind, interval) == {}
    assert journal.db.execute("SELECT COUNT(*) FROM record_versions").fetchone()[0] == 0


def test_cancel_after_first_page_leaves_only_request_evidence(journal):
    api = api_with({"list": [candle(START)], "nextPageCursor": "next"})
    cancelled = [False]

    def cancel():
        # After first page is fetched, block further pagination.
        return api.call.call_count >= 1

    result = Backfill(api, journal).run(
        "linear", "BTCUSDT", "kline", "1", START, START + 60000,
        cancel=cancel,
    )
    assert result["status"] == "paused"
    assert result["records"] == 0
    assert stored(journal, "kline", "1") == {}
    assert journal.task(key("kline", "1", START, START + 60000))["status"] == "paused"


def test_still_open_candle_may_change_between_requests(journal):
    # Open candles are never staged; a changed open candle on the gap re-query is
    # normal exchange behaviour, not a conflicting duplicate.
    server = START + 90000
    api = api_with(
        {"list": [candle(START + 60000), candle(START)]},
        {"list": [candle(START + 60000, h="12")]},
        server_ms=server,
    )
    out = Backfill(api, journal).run("linear", "BTCUSDT", "kline", "1", START, START + 120000)
    assert out["status"] == "unverified" and out["records"] == 1


# --- GAP-3 -------------------------------------------------------------------

DAY = ms(2024, 1, 1)
HEADER = b"timestamp,symbol,side,size,price,trdMatchID\n"


def trade_file(tmp_path, name, *timestamps):
    path = tmp_path / name
    body = HEADER + b"".join(
        b"%s,BTCUSDT,Buy,1,2,id%d\n" % (t, i) for i, t in enumerate(timestamps)
    )
    path.write_bytes(gzip.compress(body))
    return path


def test_archive_day_bounds_come_from_the_name():
    assert archive_utc_day("BTCUSDT2024-01-01.csv.gz") == (DAY, DAY + 86400000)
    assert archive_utc_day("a.csv.gz", "https://public.bybit.com/spot/X/X_2024-01-01.csv.gz") == (
        DAY,
        DAY + 86400000,
    )


def test_rows_on_both_edges_of_the_utc_date_are_accepted(tmp_path):
    path = trade_file(tmp_path, "BTCUSDT2024-01-01.csv.gz", b"1704067200", b"1704153599.999999999")
    records = list(iter_trade_records(path, "linear", "BTCUSDT", {"url": "u"}))
    assert [r["timestamp_ms"] for r in records] == [DAY, DAY + 86399999]


@pytest.mark.parametrize("raw", [b"1704153600", b"1704067199.999", b"1704153600000"])
def test_row_outside_the_utc_date_rejects_the_whole_file(tmp_path, raw):
    path = trade_file(tmp_path, "BTCUSDT2024-01-01.csv.gz", b"1704067200.5", raw)
    records = iter_trade_records(path, "linear", "BTCUSDT", {"url": "u"})
    with pytest.raises(ValueError, match="outside archive UTC date"):
        next(records)


@pytest.mark.parametrize(
    "name,url,message",
    [
        ("trades.csv.gz", "https://public.bybit.com/trading/X/a.csv.gz", "no UTC date"),
        ("BTCUSDT2024-01-01.csv.gz", "https://h/x/BTCUSDT2024-01-02.csv.gz", "disagree"),
        ("2024-01-01_2024-01-02.csv.gz", "u", "more than one"),
    ],
)
def test_archive_without_one_unambiguous_utc_date_is_rejected(tmp_path, name, url, message):
    path = trade_file(tmp_path, name, b"1704067200.5")
    with pytest.raises(ValueError, match=message):
        next(iter_trade_records(path, "linear", "BTCUSDT", {"url": url}))
