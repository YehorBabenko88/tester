"""Independent-review regressions at the sole public network boundary."""

from unittest.mock import Mock

import pytest
import requests

from src.ingest.transport import Transport
from tests.platform.test_public_transport_policy import ReservationFixture

URL = "https://api.bybit.com/v5/market/time"


def response(status=200, **headers):
    value = requests.Response()
    value.status_code = status
    value.headers.update(headers)
    value._content = b"{}"
    value._content_consumed = True
    return value


class Clock:
    def __init__(self):
        self.now = 100.0

    def sleep(self, delay):
        self.now += delay


def test_terminal_429_preserves_cooldown_for_next_operation(monkeypatch):
    clock = Clock()
    monkeypatch.setattr("src.ingest.transport.time.monotonic", lambda: clock.now)
    session = Mock()
    session.request.side_effect = [response(429, **{"Retry-After": "7"}), response()]
    transport = Transport(session=session, attempts=1, sleep=clock.sleep)
    with pytest.raises(requests.HTTPError):
        transport.get(URL)
    transport.get(URL)
    assert clock.now >= 107


def test_dispatch_respects_cooldown_created_during_durable_reservation(monkeypatch):
    clock = Clock()
    monkeypatch.setattr("src.ingest.transport.time.monotonic", lambda: clock.now)
    store = ReservationFixture()
    original = store.reserve
    session = Mock()
    session.request.return_value = response()
    transport = Transport(session=session, policy_store=store, sleep=clock.sleep)

    def reserve(key):
        clock.now += 5
        transport.defer(7)  # A concurrent response published a newer cooldown.
        return original(key)

    store.reserve = reserve
    transport.get(URL)
    assert clock.now >= 112


@pytest.mark.parametrize(
    "setting", ["verify", "trust_env", "auth", "cookies", "proxies", "params", "cert", "hooks"]
)
def test_unsafe_injected_requests_session_fails_before_network(setting):
    session = requests.Session()
    session.trust_env = False
    unsafe = {
        "verify": False,
        "trust_env": True,
        "auth": ("private", "credential"),
        "cookies": {"session": "private"},
        "proxies": {"https": "http://127.0.0.1:1"},
        "params": {"api_key": "private"},
        "cert": "client-credential.pem",
        "hooks": {"response": [lambda response: response]},
    }
    setattr(session, setting, unsafe[setting])
    session.request = Mock(return_value=response())
    with pytest.raises(ValueError, match="session"):
        Transport(session=session).get(URL)
    session.request.assert_not_called()


@pytest.mark.parametrize("status", [200, 302, 429, 503])
@pytest.mark.parametrize("streaming", [False, True])
def test_error_stream_close_tls_write_outage_cannot_release_pending(status, streaming):
    assert_error_stream_close_pending(ReservationFixture(), status, streaming=streaming)


def assert_error_stream_close_pending(store, status, availability=None, streaming=True):
    original = store.finish

    def finish(key, token, outcome):
        if outcome != "allow":
            raise RuntimeError("simulated database outage")
        return original(key, token, outcome)

    if availability is None:
        store.finish = finish
    value = response(status)

    def closing():
        if availability:
            availability(False)
        raise requests.exceptions.SSLError("stream close TLS")

    value.close = closing
    session = Mock()
    session.request.return_value = value
    try:
        with pytest.raises(RuntimeError):
            result = Transport(session=session, policy_store=store, attempts=1).get(
                URL, stream=streaming
            )
            if streaming and status == 200:
                result.close()
    finally:
        if availability:
            availability(True)
        store.finish = original
    state = store.read(URL.removeprefix("https://")) if hasattr(store, "read") else store.state
    assert state == "PENDING"
    fresh = Mock()
    with pytest.raises(RuntimeError, match="manual review"):
        Transport(session=fresh, policy_store=store, sleep=Mock()).get(URL)
    fresh.request.assert_not_called()


def test_stream_cancellation_drops_next_chunk_and_closes_response():
    store = ReservationFixture()
    value = response()
    cancelled = False

    def chunks(*args, **kwargs):
        nonlocal cancelled
        yield b"first"
        cancelled = True
        yield b"must not be published"

    value.iter_content = chunks
    original_close = Mock()
    value.close = original_close
    session = Mock()
    session.request.return_value = value
    stream = Transport(session=session, policy_store=store).get(
        URL, stream=True, cancel=lambda: cancelled
    )
    reader = stream.iter_content(10)
    assert next(reader) == b"first"
    with pytest.raises(InterruptedError):
        next(reader)
    original_close.assert_called_once()
    assert store.state == "CLEAR"


def test_rest_backfill_cancel_reaches_backoff_without_publishing(tmp_path):
    from src.ingest.journal import Journal
    from src.ingest.rest import API, Backfill

    cancelled = False
    session = Mock()
    session.request.side_effect = requests.Timeout("offline")

    def sleep(delay):
        nonlocal cancelled
        cancelled = True

    journal = Journal(tmp_path / "journal.sqlite")
    try:
        result = Backfill(API(Transport(session=session, sleep=sleep)), journal).run(
            "linear",
            "BTCUSDT",
            "funding",
            "1",
            1000,
            2000,
            cancel=lambda: cancelled,
        )
        assert result["status"] == "paused"
        assert result["records"] == 0
        assert session.request.call_count == 1
    finally:
        journal.close()


def test_rest_cancellation_after_json_prevents_result_publication():
    from src.ingest.rest import API

    cancelled = False
    value = response()

    def body():
        nonlocal cancelled
        cancelled = True
        return {"retCode": 0, "result": {"list": []}}

    value.json = body
    session = Mock()
    session.request.return_value = value
    api = API(Transport(session=session))
    with pytest.raises(InterruptedError):
        api.call("/time", {}, cancel=lambda: cancelled)
    assert not hasattr(api, "last_response")
    assert session.request.call_count == 1


def test_concurrent_durable_waiters_dispatch_at_distinct_rate_slots(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock
    from uuid import uuid4

    clock, barrier, state_lock = Clock(), Barrier(2), Lock()
    monkeypatch.setattr("src.ingest.transport.time.monotonic", lambda: clock.now)
    sent = []
    session = Mock()

    def request(*args, **kwargs):
        sent.append(clock.now)
        return response()

    session.request.side_effect = request
    transport = Transport(session=session, rate=2, sleep=clock.sleep)

    class DelayedPolicy(ReservationFixture):
        def reserve(self, key):
            barrier.wait(timeout=3)
            with state_lock:
                clock.now = max(clock.now, 105)
                transport.defer(3)
            return "GRANTED", str(uuid4())

        def finish(self, *args):
            return True

    transport.policy_store = DelayedPolicy()
    urls = [URL, "https://api.bybit.com/v5/market/instruments-info?category=linear"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(transport.get, url) for url in urls]
        assert all(future.result(timeout=5).status_code == 200 for future in futures)
    assert min(sent) >= 108
    assert sent[1] - sent[0] >= 0.5


def test_cancellation_observed_after_blocking_request_drops_response():
    cancelled = False
    session = Mock()
    value = response()

    def request(*args, **kwargs):
        nonlocal cancelled
        cancelled = True
        return value

    session.request.side_effect = request
    with pytest.raises(InterruptedError):
        Transport(session=session).get(URL, cancel=lambda: cancelled)
    assert session.request.call_count == 1


def test_cancelled_reader_finalizer_tls_outage_keeps_pending():
    store = ReservationFixture()
    finish = store.finish
    cancelled = False

    def outcome(key, token, result):
        if result != "allow":
            raise RuntimeError("halt write unavailable")
        return finish(key, token, result)

    store.finish = outcome
    value = response()
    value.close = Mock()

    def chunks(*args, **kwargs):
        nonlocal cancelled
        try:
            yield b"first"
            cancelled = True
            yield b"cancelled"
        finally:
            raise requests.exceptions.SSLError("iterator finalizer TLS")

    value.iter_content = chunks
    session = Mock()
    session.request.return_value = value
    stream = Transport(session=session, policy_store=store).get(
        URL, stream=True, cancel=lambda: cancelled
    )
    reader = stream.iter_content(10)
    assert next(reader) == b"first"
    with pytest.raises(RuntimeError, match="halt write unavailable"):
        next(reader)
    assert store.state == "PENDING"


def test_cancelled_blocked_read_timeout_is_reported_as_pause():
    cancelled = False
    value = response()

    def chunks(*args, **kwargs):
        nonlocal cancelled
        yield b"prefix"
        cancelled = True
        raise requests.ConnectionError("bounded read inactivity timeout")

    value.iter_content = chunks
    session = Mock()
    session.request.return_value = value
    stream = Transport(session=session).get(URL, stream=True, cancel=lambda: cancelled)
    reader = stream.iter_content(10)
    assert next(reader) == b"prefix"
    with pytest.raises(InterruptedError):
        next(reader)
