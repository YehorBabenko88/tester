"""Cancellation crosses the existing public transport and catalog paths."""

import json
import threading
from unittest.mock import Mock

import pytest
import requests

from src.api.bybit_catalog import AcquisitionError, BybitCatalogSource, acquire_instruments
from src.api.market_catalog import CatalogService
from src.ingest.transport import Transport

URL = "https://api.bybit.com/v5/market/time"


def reply(body=None, status=200):
    value = requests.Response()
    value.status_code = status
    value.url = URL
    value._content = json.dumps(
        body or {"retCode": 0, "result": {"category": "linear", "list": [], "nextPageCursor": ""}}
    ).encode()
    value._content_consumed = True
    return value


@pytest.mark.parametrize("error", [requests.Timeout, requests.ConnectionError])
def test_final_network_exception_after_cancel_is_pause_not_timeout(error):
    stopped = threading.Event()
    session = Mock()

    def request(*a, **k):
        stopped.set()
        raise error("bounded network failure")

    session.request.side_effect = request
    with pytest.raises(InterruptedError):
        Transport(session=session, attempts=1).get(URL, cancel=stopped.is_set)
    assert session.request.call_count == 1


def test_tls_fault_still_wins_over_simultaneous_cancel():
    from tests.platform.test_public_transport_policy import ReservationFixture

    stopped = threading.Event()
    store = ReservationFixture()
    session = Mock()

    def request(*a, **k):
        stopped.set()
        raise requests.exceptions.SSLError("untrusted TLS")

    session.request.side_effect = request
    with pytest.raises(requests.exceptions.SSLError):
        Transport(session=session, attempts=1, policy_store=store).get(URL, cancel=stopped.is_set)
    assert store.state == "HALTED"
    fresh = Mock()
    with pytest.raises(RuntimeError):
        Transport(session=fresh, policy_store=store).get(URL)
    fresh.request.assert_not_called()


def test_backfill_final_timeout_after_cancel_pauses_without_publication(tmp_path):
    from src.ingest.journal import Journal
    from src.ingest.rest import API, Backfill

    stopped = threading.Event()
    session = Mock()

    def request(*a, **k):
        stopped.set()
        raise requests.Timeout("bounded server time wait")

    session.request.side_effect = request
    journal = Journal(tmp_path / "cancel.sqlite")
    try:
        result = Backfill(API(Transport(session=session, attempts=1)), journal).run(
            "linear", "BTCUSDT", "kline", "1", 60000, 120000, cancel=stopped.is_set
        )
        assert result["status"] == "paused" and result["records"] == 0
        assert journal.window_records("linear", "BTCUSDT", "kline", "1", 60000, 120000) == {}
        assert session.request.call_count == 1
    finally:
        journal.close()


def cancelled_body(event, consumed):
    yield b'{"retCode":0,'
    event.set()
    yield b'"result":{"category":"linear","list":[],"nextPageCursor":""}}'
    consumed.append("after-cancel")
    yield b""


def test_instrument_cancel_reaches_stream_and_prevents_further_body_consumption():
    stopped, consumed = threading.Event(), []
    value = reply()
    value.iter_content = lambda *a, **k: cancelled_body(stopped, consumed)
    session = Mock()
    session.request.return_value = value
    source = BybitCatalogSource(transport=Transport(session=session, attempts=1))
    with pytest.raises(AcquisitionError, match="cancelled"):
        acquire_instruments(source, "linear", statuses=("Trading",), cancel=stopped)
    assert consumed == []
    assert session.request.call_count == 1


def test_instrument_cancel_during_rate_wait_prevents_next_get():
    stopped = threading.Event()
    first = reply(
        {"retCode": 0, "result": {"category": "linear", "list": [], "nextPageCursor": "next"}}
    )
    session = Mock()
    session.request.side_effect = [first, reply()]
    transport = Transport(session=session, attempts=1, rate=1, sleep=lambda _: stopped.set())
    with pytest.raises(AcquisitionError, match="cancelled"):
        acquire_instruments(
            BybitCatalogSource(transport=transport), "linear", statuses=("Trading",), cancel=stopped
        )
    assert session.request.call_count == 1


def test_service_ticker_cancel_reaches_stream_and_cannot_publish_snapshot():
    consumed = []
    session = Mock()
    source = BybitCatalogSource(transport=Transport(session=session, attempts=1, rate=1_000_000))
    service = CatalogService(source)

    def request(method, url, **kwargs):
        value = reply()
        if "/tickers" in url:
            value.iter_content = lambda *a, **k: cancelled_body(service._cancel, consumed)
        return value

    session.request.side_effect = request
    assert service.refresh("linear") is False
    assert consumed == []
    assert "linear" not in service._snapshots
    assert session.request.call_count == 6


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("persistent", [False, True])
def test_403_security_error_wins_over_simultaneous_cancel(stream, persistent):
    from tests.platform.test_public_transport_policy import ReservationFixture

    stopped = threading.Event()
    store = ReservationFixture() if persistent else None
    session = Mock()
    value = reply(status=403)

    def request(*a, **k):
        stopped.set()
        return value

    session.request.side_effect = request
    transport = Transport(session=session, policy_store=store, attempts=1)
    with pytest.raises(requests.HTTPError) as error:
        transport.get(URL, stream=stream, cancel=stopped.is_set)
    assert error.value.response.status_code == 403
    assert session.request.call_count == 1
    assert URL.removeprefix("https://") in transport.halted
    if store is not None:
        assert store.state == "HALTED"
    fresh = Mock()
    retry = Transport(session=fresh, policy_store=store) if store is not None else transport
    with pytest.raises((RuntimeError, ValueError)):
        retry.get(URL)
    assert session.request.call_count == 1
    fresh.request.assert_not_called()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("persistent", [False, True])
def test_cancelled_403_cleanup_tls_still_has_security_priority(stream, persistent):
    from tests.platform.test_public_transport_policy import ReservationFixture

    stopped = threading.Event()
    store = ReservationFixture() if persistent else None
    value = reply(status=403)
    closed = Mock(side_effect=requests.exceptions.SSLError("close TLS"))
    value.close = closed
    session = Mock()

    def request(*a, **k):
        stopped.set()
        return value

    session.request.side_effect = request
    transport = Transport(session=session, policy_store=store, attempts=1)
    with pytest.raises(requests.exceptions.SSLError):
        transport.get(URL, stream=stream, cancel=stopped.is_set)
    assert session.request.call_count == 1
    closed.assert_called_once()
    if store is not None:
        assert store.state == "HALTED"
