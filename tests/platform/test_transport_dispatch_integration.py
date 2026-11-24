"""The sole Transport delegates scope dispatch to existing durable authority."""

import threading
from unittest.mock import Mock
from uuid import uuid4

import pytest
import requests

from src.ingest.transport import Transport
from tests.platform.test_public_transport_edges import Clock, response

URL = "https://api.bybit.com/v5/market/time"
OTHER = "https://api.bybit.com/v5/market/kline"


class DispatchStore:
    """Deterministic protocol fixture, not PostgreSQL durability evidence."""

    def __init__(self, clock):
        self.clock = clock
        self.token = None
        self.not_before = 0
        self.finish = Mock()

    def reserve(self, key):
        return "GRANTED", str(uuid4())

    def reserve_dispatch(self):
        if self.token is not None:
            return "PENDING", None, 0
        if self.not_before > self.clock.now:
            return "WAIT", None, self.not_before - self.clock.now
        self.token = str(uuid4())
        return "GRANTED", self.token, 0

    def finish_dispatch(self, token, cooldown_seconds=0):
        assert token == self.token
        self.token = None
        self.not_before = max(self.not_before, self.clock.now + max(0.5, cooldown_seconds))


def setup(monkeypatch):
    clock = Clock()
    monkeypatch.setattr("src.ingest.transport.time.monotonic", lambda: clock.now)
    store = DispatchStore(clock)
    return clock, store


@pytest.mark.parametrize("first_status,spacing", [(200, 0.5), (429, 7)])
def test_fresh_transport_shares_scope_spacing_and_retry_after(monkeypatch, first_status, spacing):
    clock, store = setup(monkeypatch)
    first, second = Mock(), Mock()
    first.request.return_value = response(first_status, **{"Retry-After": "7"})
    second.request.return_value = response()
    transport = Transport(session=first, policy_store=store, attempts=1, sleep=clock.sleep)
    if first_status == 429:
        with pytest.raises(requests.HTTPError):
            transport.get(URL)
    else:
        transport.get(URL)
    second_transport = Transport(session=second, policy_store=store, attempts=1, sleep=clock.sleep)
    if spacing > 5:
        with pytest.raises(RuntimeError, match="cooldown active"):
            second_transport.get(OTHER)
        second.request.assert_not_called()
        clock.now += spacing
    second_transport.get(OTHER)
    assert clock.now >= 100 + spacing
    assert first.request.call_count == second.request.call_count == 1


def test_scope_wait_cancellation_never_sends(monkeypatch):
    clock, store = setup(monkeypatch)
    store.not_before = clock.now + 1
    stopped = threading.Event()
    session = Mock()
    session.request.return_value = response()
    with pytest.raises(InterruptedError):
        Transport(session=session, policy_store=store, sleep=lambda _: stopped.set()).get(
            URL, cancel=stopped.is_set
        )
    session.request.assert_not_called()


def test_crashed_scope_or_failed_release_denies_fresh_instances(monkeypatch):
    clock, store = setup(monkeypatch)
    store.token = str(uuid4())
    session = Mock()
    session.request.return_value = response()
    with pytest.raises(RuntimeError):
        Transport(session=session, policy_store=store, sleep=clock.sleep).get(URL)
    session.request.assert_not_called()
    store.token = None
    store.finish_dispatch = Mock(side_effect=RuntimeError("commit unavailable"))
    with pytest.raises(RuntimeError, match="commit unavailable"):
        Transport(session=session, policy_store=store, sleep=clock.sleep).get(URL)
    assert store.token is not None
    assert session.request.return_value.raw is None
    fresh = Mock()
    fresh.request.return_value = response()
    with pytest.raises(RuntimeError):
        Transport(session=fresh, policy_store=store, sleep=clock.sleep).get(OTHER)
    fresh.request.assert_not_called()


def test_scope_completion_failure_owns_response_and_preserves_close_tls(monkeypatch):
    clock, store = setup(monkeypatch)
    store.finish_dispatch = Mock(side_effect=RuntimeError("scope commit unavailable"))
    value = response()
    original_close = Mock(side_effect=requests.exceptions.SSLError("late close TLS"))
    value.close = original_close
    session = Mock()
    session.request.return_value = value
    transport = Transport(session=session, policy_store=store, sleep=clock.sleep)
    with pytest.raises(requests.exceptions.SSLError):
        transport.get(URL, stream=True)
    original_close.assert_called_once()
    assert store.finish.call_args.args[-1] == "tls_failure"
    assert transport.last_observation.kind == "tls_failure"
    assert store.finish_dispatch.call_count == 1
    for endpoint in [URL, OTHER]:
        fresh = Mock()
        fresh.request.return_value = response()
        with pytest.raises(RuntimeError):
            Transport(session=fresh, policy_store=store, sleep=clock.sleep).get(endpoint)
        fresh.request.assert_not_called()


def test_network_tls_is_not_masked_by_scope_completion_failure(monkeypatch):
    clock, store = setup(monkeypatch)
    store.finish_dispatch = Mock(side_effect=RuntimeError("scope commit unavailable"))
    session = Mock()
    session.request.side_effect = requests.exceptions.SSLError("network TLS")
    transport = Transport(session=session, policy_store=store, sleep=clock.sleep)
    with pytest.raises(requests.exceptions.SSLError):
        transport.get(URL)
    assert store.finish.call_args.args[-1] == "tls_failure"
    assert transport.last_observation.kind == "tls_failure"
    assert store.finish_dispatch.call_count == 1


@pytest.fixture
def durable_policy():
    from tests.platform.test_public_transport_policy import durable_policy as fixture

    yield from fixture.__wrapped__()


def test_actual_pg_scope_completion_outage_then_close_tls_blocks_all_endpoints(
    durable_policy, monkeypatch
):
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from src.api.db import connect

    policy, owner, scope = durable_policy
    database = conninfo_to_dict(owner.dsn)["dbname"]
    admin = make_conninfo(owner.dsn, dbname="postgres")
    original_finish = policy.finish_dispatch

    def availability(enabled):
        with connect(admin) as conn:
            conn.execute(
                sql.SQL(
                    "ALTER DATABASE {} ALLOW_CONNECTIONS " + ("true" if enabled else "false")
                ).format(sql.Identifier(database))
            )

    def fail(token, cooldown_seconds=0):
        availability(False)
        original_finish(token, cooldown_seconds)

    monkeypatch.setattr(policy, "finish_dispatch", fail)
    value = response()
    closed = Mock(side_effect=requests.exceptions.SSLError("close TLS"))
    value.close = closed
    session = Mock()
    session.request.return_value = value
    transport = Transport(session=session, policy_store=policy)
    try:
        with pytest.raises(RuntimeError):
            transport.get(URL, stream=True)
    finally:
        availability(True)
        monkeypatch.setattr(policy, "finish_dispatch", original_finish)
    closed.assert_called_once()
    assert transport.last_observation.kind == "tls_failure"
    assert policy.read(URL.removeprefix("https://")) == "PENDING"
    assert policy.reserve_dispatch()[0] == "PENDING"
    for endpoint in [URL, OTHER]:
        fresh = Mock()
        with pytest.raises(RuntimeError):
            Transport(session=fresh, policy_store=policy, sleep=Mock()).get(endpoint)
        fresh.request.assert_not_called()


@pytest.mark.parametrize("fault", ["403", "tls"])
@pytest.mark.parametrize("persistent", [False, True])
def test_observer_failure_cannot_prevent_security_halt(fault, persistent):
    from tests.platform.test_public_transport_policy import ReservationFixture

    store = ReservationFixture() if persistent else None
    session = Mock()
    value = response(403)
    closer = Mock()
    value.close = closer
    if fault == "tls":
        session.request.side_effect = requests.exceptions.SSLError("untrusted")
    else:
        session.request.return_value = value
    transport = Transport(
        session=session,
        policy_store=store,
        attempts=1,
        event_sink=Mock(side_effect=ValueError("observer failed")),
    )
    with pytest.raises(ValueError, match="observer failed"):
        transport.get(URL)
    assert URL.removeprefix("https://") in transport.halted
    if store is not None:
        assert store.state == "HALTED"
    if fault == "403":
        closer.assert_called_once()
    fresh = Mock()
    retry = Transport(session=fresh, policy_store=store) if store is not None else transport
    count = session.request.call_count
    with pytest.raises((ValueError, RuntimeError)):
        retry.get(URL)
    assert session.request.call_count == count
    fresh.request.assert_not_called()


def test_observer_failure_releases_known_completed_scope(monkeypatch):
    clock, store = setup(monkeypatch)
    value = response()
    closed = Mock()
    value.close = closed
    session = Mock()
    session.request.return_value = value
    transport = Transport(
        session=session,
        policy_store=store,
        sleep=clock.sleep,
        event_sink=Mock(side_effect=ValueError("observer failed")),
    )
    with pytest.raises(ValueError, match="observer failed"):
        transport.get(URL)
    assert store.token is None
    assert store.not_before >= 100.5
    closed.assert_called_once()


def test_actual_pg_fresh_transports_share_send_spacing(durable_policy):
    import time

    policy, owner, scope = durable_policy
    sent = []
    for endpoint in [URL, OTHER]:
        session = Mock()

        def request(*a, **k):
            sent.append(time.monotonic())
            return response()

        session.request.side_effect = request
        Transport(session=session, policy_store=policy, attempts=1).get(endpoint)
    assert sent[1] - sent[0] >= 0.49


def test_actual_pg_fresh_process_honors_429_without_resetting_cooldown(durable_policy):
    import json
    import os
    import subprocess
    import sys

    policy, owner, scope = durable_policy
    first = Mock()
    first.request.return_value = response(429, **{"Retry-After": "10"})
    with pytest.raises(requests.HTTPError):
        Transport(session=first, policy_store=policy, attempts=1).get(URL)
    script = """import os,json;from unittest.mock import Mock;from src.api.transport_policy import PostgresTransportPolicy;from src.ingest.transport import Transport
p=PostgresTransportPolicy(os.environ['TEST_PUBLIC_POLICY_DSN'],os.environ['TEST_DISPATCH_SCOPE']);s=Mock()
try:Transport(session=s,policy_store=p,attempts=1).get('https://api.bybit.com/v5/market/kline')
except RuntimeError:print(json.dumps({'network_calls':s.request.call_count}))
else:raise AssertionError('cooldown bypassed')
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "TEST_DISPATCH_SCOPE": str(scope)},
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert json.loads(result.stdout) == {"network_calls": 0}
    state, token, delay = policy.reserve_dispatch()
    assert state == "WAIT" and token is None and 0 < delay <= 10


def test_actual_pg_death_inside_network_keeps_scope_pending(durable_policy):
    import os
    import subprocess
    import sys

    policy, owner, scope = durable_policy
    script = """import os;from unittest.mock import Mock;from src.api.transport_policy import PostgresTransportPolicy;from src.ingest.transport import Transport
p=PostgresTransportPolicy(os.environ['TEST_PUBLIC_POLICY_DSN'],os.environ['TEST_DISPATCH_SCOPE']);s=Mock();s.request.side_effect=lambda *a,**k:os._exit(0)
Transport(session=s,policy_store=p,attempts=1).get('https://api.bybit.com/v5/market/time')
raise AssertionError('network hook never executed')
"""
    subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "TEST_DISPATCH_SCOPE": str(scope)},
        timeout=15,
        check=True,
    )
    assert policy.reserve_dispatch()[0] == "PENDING"
    fresh = Mock()
    with pytest.raises(RuntimeError):
        Transport(session=fresh, policy_store=policy, sleep=Mock()).get(OTHER)
    fresh.request.assert_not_called()
    owner.reset(None)
    state, token, delay = policy.reserve_dispatch()
    assert state == "GRANTED"
    policy.finish_dispatch(token)


@pytest.mark.parametrize(
    "reply", [("GRANTED", None, 0), ("WAIT", None, float("nan")), ("PENDING", str(uuid4()), 0)]
)
def test_malformed_scope_authority_never_sends(monkeypatch, reply):
    clock, store = setup(monkeypatch)
    store.reserve_dispatch = Mock(return_value=reply)
    session = Mock()
    with pytest.raises(RuntimeError):
        Transport(session=session, policy_store=store, sleep=clock.sleep).get(URL)
    session.request.assert_not_called()


def test_session_is_revalidated_after_shared_admission_wait(monkeypatch):
    from src.ingest.transport import UnsafePublicSession

    clock, store = setup(monkeypatch)
    store.not_before = clock.now + 0.5
    session = requests.Session()
    session.trust_env = False
    session.request = Mock(return_value=response())

    def sleep(delay):
        clock.sleep(delay)
        session.auth = ("forbidden", "credential")

    with pytest.raises(UnsafePublicSession):
        Transport(session=session, policy_store=store, sleep=sleep).get(URL)
    session.request.assert_not_called()
    assert store.token is None
